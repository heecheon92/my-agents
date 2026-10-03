"""Offline operator notification creation, isolation, and retry contract."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from my_agents.api import create_app
from my_agents.auth.email import InMemoryAuthEmailSender, get_local_auth_email_outbox
from my_agents.auth.models import RegistrationNotificationModel, UserModel
from my_agents.auth.notifications import deliver_pending, enqueue_registration
from my_agents.persistence.database import get_database_session
from my_agents.settings import Settings

from .conftest import latest_auth_email_token
from .test_group_invitations_api import _create_group, _signup_login


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'notify.db'}")
    monkeypatch.setenv("MY_AGENTS_AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("MY_AGENTS_REGISTRATION_NOTIFICATION_EMAIL", "operator@example.com")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("MY_AGENTS_AUTH_EMAIL_MODE", "local")
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_GUEST_CODE_AUTO_APPROVAL", "true")
    monkeypatch.setenv("MY_AGENTS_ACCOUNT_SIGNUP_AUTO_APPROVAL", "false")
    return TestClient(create_app())


@pytest.fixture
def db(client):
    generator = get_database_session()
    session = next(generator)
    yield session
    generator.close()


def signup(client):
    return client.post(
        "/auth/signup",
        json={
            "email": "person@example.com",
            "nickname": "Person",
            "password": "long test password",
        },
    )


def test_pending_signup_queues_once_and_delivers_outside_dev_outbox(client, db):
    assert signup(client).status_code == 201
    assert signup(client).status_code == 409
    jobs = db.scalars(select(RegistrationNotificationModel)).all()
    assert len(jobs) == 1
    assert jobs[0].approval_status == "pending"
    sender = InMemoryAuthEmailSender()
    assert deliver_pending(db, sender) == 1
    assert deliver_pending(db, sender) == 0
    recipient, subject, body = sender.registration_notifications[0]
    assert recipient == "operator@example.com"
    assert "registered_signup" in subject
    assert "person@example.com" in body and "pending" in body
    assert "long test password" not in body
    assert sender.messages() == ()
    db.refresh(jobs[0])
    assert jobs[0].account_email is None and jobs[0].recipient_email is None
    assert jobs[0].sent_at is not None


def test_guest_request_and_resend_do_not_notify_but_redemption_does(client, db):
    for _ in range(2):
        assert (
            client.post("/auth/guest/request", json={"email": "Guest@Example.com"}).status_code
            == 200
        )
    assert db.scalars(select(RegistrationNotificationModel)).all() == []
    code = get_local_auth_email_outbox().messages()[-1].token
    assert client.post("/auth/guest/login", json={"code": code}).status_code == 200
    assert client.post("/auth/guest/login", json={"code": code}).status_code == 400
    job = db.scalar(select(RegistrationNotificationModel))
    assert job.kind == "guest_redemption"
    assert job.account_email == "guest@example.com"
    assert job.guest_expires_at is not None
    assert len(db.scalars(select(RegistrationNotificationModel)).all()) == 1


def test_failed_delivery_preserves_account_and_retries_without_leaking_errors(client, db, caplog):
    class BrokenSender:
        def send_registration_notification(self, **kwargs):
            raise RuntimeError("PRIVATE_PROVIDER_DETAIL")

    assert signup(client).status_code == 201
    assert deliver_pending(db, BrokenSender()) == 0
    job = db.scalar(select(RegistrationNotificationModel))
    assert job.attempts == 1 and job.sent_at is None
    assert db.get(UserModel, job.user_id) is not None
    sender = InMemoryAuthEmailSender()
    assert deliver_pending(db, sender) == 0  # backoff is respected
    assert "PRIVATE_PROVIDER_DETAIL" not in caplog.text
    job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert deliver_pending(db, sender) == 1
    db.refresh(job)
    assert job.attempts == 2


def test_active_lease_skips_delivery_and_expired_lease_recovers(client, db):
    assert signup(client).status_code == 201
    job = db.scalar(select(RegistrationNotificationModel))
    job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
    job.lease_token = "old-worker"
    db.commit()
    sender = InMemoryAuthEmailSender()
    assert deliver_pending(db, sender) == 0
    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert deliver_pending(db, sender) == 1


def test_queue_rolls_back_with_account(db):
    user = UserModel(
        id="rolled-back", email="rollback@example.com", nickname="Test", password_hash="unused"
    )
    db.add(user)
    enqueue_registration(
        db,
        user,
        recipient="operator@example.com",
        account_email=user.email,
        kind="registered_signup",
    )
    db.flush()
    db.rollback()
    assert db.get(UserModel, user.id) is None
    assert db.scalars(select(RegistrationNotificationModel)).all() == []


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_or_blank_disables_queue(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MY_AGENTS_REGISTRATION_NOTIFICATION_EMAIL", raising=False)
    else:
        monkeypatch.setenv("MY_AGENTS_REGISTRATION_NOTIFICATION_EMAIL", value)
    assert Settings(_env_file=None).registration_notification_email is None
    client = TestClient(create_app())
    assert signup(client).status_code == 201
    generator = get_database_session()
    try:
        assert next(generator).scalars(select(RegistrationNotificationModel)).all() == []
    finally:
        generator.close()


def test_invalid_recipient_rejected(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_REGISTRATION_NOTIFICATION_EMAIL", "not-an-email")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invitation_signup_notifies_new_user_only(monkeypatch):
    monkeypatch.setenv("MY_AGENTS_REGISTRATION_NOTIFICATION_EMAIL", "operator@example.com")
    monkeypatch.setenv("MY_AGENTS_SESSION_COOKIE_SECURE", "false")
    owner = TestClient(create_app())
    _signup_login(owner, "owner@example.com")
    group_id = _create_group(owner)
    assert (
        owner.post(
            f"/groups/{group_id}/invitations",
            json={"email": "invited@example.com", "role": "viewer"},
        ).status_code
        == 201
    )
    token = latest_auth_email_token("invited@example.com", purpose="group_invitation")
    visitor = TestClient(create_app())
    response = visitor.post(
        "/group-invitations/signup",
        json={"token": token, "nickname": "Invited", "password": "long test password"},
    )
    assert response.status_code == 201
    generator = get_database_session()
    try:
        jobs = next(generator).scalars(select(RegistrationNotificationModel)).all()
        assert sorted(job.kind for job in jobs) == ["invitation_signup", "registered_signup"]
        assert (
            next(job for job in jobs if job.kind == "invitation_signup").account_email
            == "invited@example.com"
        )
    finally:
        generator.close()


def test_disabled_worker_never_constructs_sender(monkeypatch):
    import asyncio

    from my_agents.auth.notifications import notification_loop

    def unexpected_sender(settings):
        raise AssertionError("disabled worker must not initialize email delivery")

    monkeypatch.setattr("my_agents.auth.notifications.build_auth_email_sender", unexpected_sender)
    asyncio.run(notification_loop(Settings(_env_file=None)))


@pytest.mark.parametrize("sender_class", ["SmtpAuthEmailSender", "ResendHttpAuthEmailSender"])
def test_operator_alert_uses_existing_transport(monkeypatch, sender_class):
    from my_agents.auth import email

    # Bypass constructor credentials: verify the narrow forwarding contract without network.
    sender = object.__new__(getattr(email, sender_class))
    calls = []
    monkeypatch.setattr(sender, "_send", lambda **kwargs: calls.append(kwargs))
    sender.send_registration_notification(
        recipient_email="operator@example.com", subject="Created", body="Account created"
    )
    assert calls == [
        {"recipient_email": "operator@example.com", "subject": "Created", "body": "Account created"}
    ]


@pytest.mark.parametrize("recipient", ["", "operator@example.com"])
def test_lifespan_starts_worker_only_when_configured(monkeypatch, recipient):
    called = []

    async def worker(settings):
        called.append(str(settings.registration_notification_email))

    monkeypatch.setenv("MY_AGENTS_REGISTRATION_NOTIFICATION_EMAIL", recipient)
    monkeypatch.setattr("my_agents.auth.notifications.notification_loop", worker)
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
    assert called == ([recipient] if recipient else [])


def test_enabled_worker_drains_persisted_queue(client, db, monkeypatch):
    import asyncio

    from my_agents.auth.notifications import notification_loop
    from my_agents.settings import get_settings

    assert signup(client).status_code == 201
    sender = InMemoryAuthEmailSender()
    monkeypatch.setattr(
        "my_agents.auth.notifications.build_auth_email_sender", lambda settings: sender
    )

    async def finish_after_batch(seconds):
        assert seconds == 30
        raise asyncio.CancelledError

    monkeypatch.setattr("my_agents.auth.notifications.asyncio.sleep", finish_after_batch)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(notification_loop(get_settings()))
    assert len(sender.registration_notifications) == 1
    db.expire_all()
    assert db.scalar(select(RegistrationNotificationModel)).sent_at is not None


def test_notification_migration_downgrade_and_upgrade(tmp_path, monkeypatch):
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from alembic import command

    url = f"sqlite+pysqlite:///{tmp_path / 'notification-migration.db'}"
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "20260930_0036")
    engine = create_engine(url)
    assert "registration_notifications" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "registration_notifications" in inspect(engine).get_table_names()
    engine.dispose()


def test_memory_worker_cannot_rollback_flushed_request(monkeypatch):
    import asyncio

    from my_agents.auth.notifications import notification_loop
    from my_agents.settings import get_settings

    monkeypatch.setenv("MY_AGENTS_REGISTRATION_NOTIFICATION_EMAIL", "operator@example.com")
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    generator = get_database_session()
    db = next(generator)
    try:
        db.add(
            UserModel(
                id="inflight", email="inflight@example.com", nickname="Test", password_hash="unused"
            )
        )
        db.flush()
        asyncio.run(notification_loop(get_settings()))
        db.commit()
        db.expire_all()
        assert db.get(UserModel, "inflight") is not None
    finally:
        generator.close()


def test_shutdown_waits_for_current_send_and_does_not_send_next(client, db, monkeypatch):
    import asyncio
    from threading import Event

    from my_agents.auth.notifications import notification_loop
    from my_agents.settings import get_settings

    assert signup(client).status_code == 201
    assert (
        client.post(
            "/auth/signup",
            json={
                "email": "second@example.com",
                "nickname": "Second",
                "password": "long test password",
            },
        ).status_code
        == 201
    )
    entered, release = Event(), Event()
    sent = []

    class BlockingSender:
        def send_registration_notification(self, **kwargs):
            sent.append(kwargs)
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(
        "my_agents.auth.notifications.build_auth_email_sender", lambda settings: BlockingSender()
    )

    async def exercise():
        task = asyncio.create_task(notification_loop(get_settings()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert len(sent) == 1
    db.expire_all()
    jobs = db.scalars(select(RegistrationNotificationModel)).all()
    assert sorted(job.attempts for job in jobs) == [0, 1]
    assert sum(job.sent_at is not None for job in jobs) == 1
