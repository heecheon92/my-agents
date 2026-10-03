"""One lifetime guest trial per email, including concurrent and post-cleanup attempts."""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session

from my_agents.api import create_app
from my_agents.auth.email import InMemoryAuthEmailSender, get_local_auth_email_outbox
from my_agents.auth.guest_cleanup import delete_expired_guest
from my_agents.auth.guest_policy import (
    GuestIpRateLimited,
    GuestRequestSuppressed,
    fingerprint,
    initialize_guest_policy,
    reset_trial,
    take_budget,
)
from my_agents.auth.models import (
    GuestAccessCodeModel,
    GuestAccessRequestModel,
    GuestDeletionAuditModel,
    GuestTrialModel,
    RegistrationNotificationModel,
    SessionModel,
    UserModel,
)
from my_agents.auth.service import AuthService, InvalidAuthTokenError
from my_agents.conversations.models import ConversationModel, MessageModel
from my_agents.persistence.database import Base
from my_agents.persistence.langgraph import LangGraphPersistenceResources
from my_agents.persistence.models import import_all_models
from my_agents.settings import Settings

KEY = "test-only-guest-cleanup-hmac-key-32-bytes"
EMAIL = "guest@example.com"


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'guest-policy.db'}", connect_args={"timeout": 10}
    )

    @event.listens_for(engine, "connect")
    def enable_fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    import_all_models()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def service(db, sender=None):
    return AuthService(
        db,
        email_sender=sender or InMemoryAuthEmailSender(),
        notification_email="operator@example.com",
    )


def issue(auth, email=EMAIL):
    return auth.issue_guest_access_code(email=email, ttl=timedelta(minutes=15)).code


def redeem(auth, code):
    return auth.redeem_guest_access_code(code=code, access_ttl=timedelta(days=1))


def allow_resend(db):
    db.execute(
        update(GuestTrialModel).values(last_sent_at=datetime.now(UTC) - timedelta(minutes=2))
    )
    db.commit()


def test_repeat_trial_reuses_account_expiry_quota_and_notifies_only_once(engine):
    with Session(engine) as db:
        auth = service(db)
        first = redeem(auth, issue(auth, "Guest@Example.com"))
        user_id, expires = first.user.id, first.user.guest_expires_at
        conv = ConversationModel(owner_user_id=user_id, title="Trial")
        db.add(conv)
        db.flush()
        db.add(MessageModel(conversation_id=conv.id, role="user", content="Counted prompt"))
        db.commit()
        allow_resend(db)
        second = redeem(auth, issue(auth))
        assert second.user.id == user_id
        assert second.user.guest_expires_at == expires
        assert second.session_token != first.session_token
        assert db.scalar(select(func.count()).select_from(MessageModel)) == 1
        assert db.scalar(select(func.count()).select_from(UserModel)) == 1
        assert db.scalar(select(func.count()).select_from(RegistrationNotificationModel)) == 1


def test_used_email_stays_ineligible_after_cleanup_and_preserves_pending_code_privacy(engine):
    with Session(engine) as db:
        auth = service(db)
        first = redeem(auth, issue(auth))
        user_id = first.user.id
        allow_resend(db)
        pending_code = issue(auth)
        first.user.guest_expires_at = datetime.now(UTC) - timedelta(days=2)
        db.commit()
        assert (
            delete_expired_guest(
                db,
                user_id=user_id,
                cutoff=datetime.now(UTC) - timedelta(days=1),
                email_hmac_key=KEY,
                resources=LangGraphPersistenceResources(),
            )
            == "deleted"
        )
        assert db.scalar(select(func.count()).select_from(GuestAccessRequestModel)) == 0
        assert db.scalar(select(func.count()).select_from(GuestAccessCodeModel)) == 0
        trial = db.get(GuestTrialModel, fingerprint(EMAIL, KEY))
        assert trial.redeemed_at is not None and trial.guest_user_id is None
        with pytest.raises(GuestRequestSuppressed):
            issue(auth)
        with pytest.raises(InvalidAuthTokenError):
            redeem(auth, pending_code)


def test_code_expiry_resend_and_operator_reset_cannot_revive_old_codes(engine):
    with Session(engine) as db:
        auth = service(db)
        old_code = issue(auth)
        with pytest.raises(GuestRequestSuppressed, match="cooldown"):
            issue(auth)
        allow_resend(db)
        new_code = issue(auth)
        with pytest.raises(InvalidAuthTokenError):
            redeem(auth, old_code)
        session = redeem(auth, new_code)
        old_user_id = session.user.id
        allow_resend(db)
        before_reset = issue(auth)
        reset_trial(db, email=EMAIL, key=KEY)
        with pytest.raises(InvalidAuthTokenError):
            redeem(auth, before_reset)
        new_session = redeem(auth, issue(auth))
        assert new_session.user.id != old_user_id
        assert db.get(GuestTrialModel, fingerprint(EMAIL, KEY)).generation == 1
        assert all(
            s.revoked_at
            for s in db.scalars(select(SessionModel).where(SessionModel.user_id == old_user_id))
        )


def test_two_simultaneous_requests_send_one_code(engine):
    sender = InMemoryAuthEmailSender()

    def attempt():
        with Session(engine) as db:
            try:
                service(db, sender).issue_and_send_guest_access_code(
                    email=EMAIL, ttl=timedelta(minutes=15)
                )
                return "issued"
            except GuestRequestSuppressed:
                return "suppressed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["issued", "suppressed"]
    assert len(sender.messages()) == 1
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(GuestTrialModel)) == 1
        assert db.scalar(select(func.count()).select_from(GuestAccessRequestModel)) == 1


def test_same_code_concurrent_redemption_creates_one_account(engine):
    with Session(engine) as db:
        code = issue(service(db))

    def attempt():
        with Session(engine) as db:
            try:
                return redeem(service(db), code).user.id
            except InvalidAuthTokenError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(UserModel)) == 1
        assert db.scalar(select(func.count()).select_from(RegistrationNotificationModel)) == 1


def test_different_valid_codes_cannot_create_two_accounts(engine):
    with Session(engine) as db:
        code = issue(service(db))
        row = db.scalar(select(GuestAccessCodeModel))
        other_code = "second-valid-code-for-concurrency-test"
        db.add(
            GuestAccessCodeModel(
                id=str(uuid.uuid4()),
                request_id=row.request_id,
                email_fingerprint=row.email_fingerprint,
                generation=row.generation,
                code_hash=hashlib.sha256(other_code.encode()).hexdigest(),
                expires_at=row.expires_at,
            )
        )
        db.commit()

    def attempt(code):
        with Session(engine) as db:
            return redeem(service(db), code).user.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [code, other_code]))
    assert len(set(results)) == 1


def test_registered_email_is_blocked_at_issuance_and_redemption(engine):
    with Session(engine) as db:
        auth = service(db)
        code = issue(auth)
        db.add(
            UserModel(
                id="registered",
                email=EMAIL,
                nickname="Registered",
                password_hash="unused",
                account_type="registered",
            )
        )
        db.commit()
        allow_resend(db)
        with pytest.raises(GuestRequestSuppressed):
            issue(auth)
        with pytest.raises(InvalidAuthTokenError):
            redeem(auth, code)
        assert db.scalar(select(func.count()).select_from(UserModel)) == 1


def test_shared_ip_budget_is_atomic_across_sessions(engine):
    def attempt():
        with Session(engine) as db:
            allowed = take_budget(
                db, key=KEY, action="test-ip", subject="same-origin", limit=2, window_seconds=3600
            )
            db.commit()
            return allowed

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(lambda _: attempt(), range(6))) == 2


def test_email_daily_limit_and_login_ip_limit(engine):
    with Session(engine) as db:
        settings = Settings(
            _env_file=None,
            MY_AGENTS_GUEST_CODE_EMAIL_DAILY_LIMIT=2,
            MY_AGENTS_GUEST_LOGIN_IP_WINDOW_LIMIT=2,
        )
        auth = AuthService(db, guest_settings=settings)
        issue(auth)
        allow_resend(db)
        issue(auth)
        allow_resend(db)
        with pytest.raises(GuestRequestSuppressed, match="daily"):
            issue(auth)
        for _ in range(2):
            with pytest.raises(InvalidAuthTokenError):
                auth.redeem_guest_access_code(
                    code="invalid", access_ttl=timedelta(days=1), client_identifier="same-origin"
                )
        with pytest.raises(GuestIpRateLimited):
            auth.redeem_guest_access_code(
                code="invalid", access_ttl=timedelta(days=1), client_identifier="same-origin"
            )


def test_changed_key_fails_closed(engine):
    with Session(engine) as db:
        issue(service(db))
        settings = Settings(
            _env_file=None,
            MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY="different-test-key-with-at-least-32-bytes",
        )
        with pytest.raises(RuntimeError, match="key changed"):
            issue(AuthService(db, guest_settings=settings))


def test_generic_request_response_hides_ineligible_email_and_ip_limit_is_explicit(
    engine, monkeypatch
):
    monkeypatch.setenv("MY_AGENTS_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("MY_AGENTS_GUEST_ACCESS_ENABLED", "true")
    monkeypatch.setenv("MY_AGENTS_GUEST_CODE_AUTO_APPROVAL", "true")
    monkeypatch.setenv("MY_AGENTS_GUEST_REQUEST_IP_HOURLY_LIMIT", "3")
    with Session(engine) as db:
        db.add(
            UserModel(
                id="registered",
                email="registered@example.com",
                nickname="Registered",
                password_hash="unused",
                account_type="registered",
            )
        )
        db.commit()
    client = TestClient(create_app())
    for email in [EMAIL, EMAIL, "registered@example.com"]:
        response = client.post("/auth/guest/request", json={"email": email})
        assert response.status_code == 200 and response.json() == {"status": "accepted"}
    assert len(get_local_auth_email_outbox().messages()) == 1
    assert client.post("/auth/guest/request", json={"email": "new@example.com"}).status_code == 429


def test_backfill_keeps_oldest_active_trial_and_retains_deleted_email_history(engine):
    now = datetime.now(UTC)
    with Session(engine) as db:
        users = []
        for i in range(2):
            user = UserModel(
                id=f"legacy-{i}",
                email=f"legacy-{i}@guest.example.com",
                nickname="Guest",
                password_hash="unused",
                account_type="guest",
                guest_expires_at=now + timedelta(hours=12 + i),
                created_at=now - timedelta(hours=2 - i),
            )
            request = GuestAccessRequestModel(id=f"request-{i}", email=EMAIL, status="consumed")
            db.add_all([user, request])
            db.flush()
            db.add(
                GuestAccessCodeModel(
                    id=f"code-{i}",
                    request_id=request.id,
                    code_hash=f"hash-{i}",
                    guest_user_id=user.id,
                    consumed_at=now - timedelta(hours=2 - i),
                    expires_at=now,
                )
            )
            db.add(
                SessionModel(
                    id=f"session-{i}",
                    user_id=user.id,
                    token_hash=f"token-{i}",
                    csrf_token_hash=f"csrf-{i}",
                    expires_at=user.guest_expires_at,
                )
            )
            users.append(user)
        db.add(
            GuestDeletionAuditModel(
                guest_user_id="deleted",
                email_fingerprint=fingerprint("deleted@example.com", KEY),
                guest_created_at=now - timedelta(days=3),
                guest_expired_at=now - timedelta(days=2),
                deleted_at=now,
                counts_json="{}",
                reason="guest_expired",
            )
        )
        db.commit()
        expected_expiry = users[0].guest_expires_at
        initialize_guest_policy(db, KEY)
        db.commit()
        trial = db.get(GuestTrialModel, fingerprint(EMAIL, KEY))
        assert trial.guest_user_id == "legacy-0"
        assert db.get(UserModel, "legacy-0").guest_expires_at == expected_expiry
        assert db.get(UserModel, "legacy-1").guest_expires_at <= now.replace(
            tzinfo=None
        ) + timedelta(seconds=2)
        assert db.get(SessionModel, "session-1").revoked_at is not None
        assert db.get(SessionModel, "session-0").revoked_at is None
        assert (
            db.get(UserModel, "legacy-1") is not None
        )  # content is not purged during reconciliation
        with pytest.raises(GuestRequestSuppressed):
            issue(service(db), "deleted@example.com")
        assert all(code.email_fingerprint for code in db.scalars(select(GuestAccessCodeModel)))


def test_legacy_unassigned_guest_session_cannot_bypass_bootstrapped_policy(engine):
    with Session(engine) as db:
        auth = service(db)
        keeper = redeem(auth, issue(auth))
        # Model an old application instance creating a duplicate after the new bootstrap.
        request = GuestAccessRequestModel(id="late-request", email=EMAIL, status="consumed")
        user = UserModel(
            id="late-user",
            email="late@guest.example.com",
            nickname="Guest",
            password_hash="unused",
            account_type="guest",
            guest_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add_all([request, user])
        db.flush()
        db.add(
            GuestAccessCodeModel(
                id="late-code",
                request_id=request.id,
                code_hash="legacy-hash",
                guest_user_id=user.id,
                consumed_at=datetime.now(UTC),
                expires_at=datetime.now(UTC),
            )
        )
        db.add(
            SessionModel(
                id="late-session",
                user_id=user.id,
                token_hash=hashlib.sha256(b"late-session-token").hexdigest(),
                csrf_token_hash="test-csrf",
                expires_at=user.guest_expires_at,
            )
        )
        db.commit()
        from my_agents.auth.service import InvalidSessionError

        with pytest.raises(InvalidSessionError):
            auth.authenticate_session("late-session-token")
        assert auth.authenticate_session(keeper.session_token).user_id == keeper.user.id


def test_redemption_failure_rolls_back_code_consumption_and_trial(engine):
    from sqlalchemy import event

    with Session(engine) as db:
        auth = service(db)
        code = issue(auth)

        def fail_session(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO sessions"):
                raise RuntimeError("injected session failure")

        event.listen(engine, "before_cursor_execute", fail_session)
        try:
            with pytest.raises(RuntimeError):
                redeem(auth, code)
        finally:
            event.remove(engine, "before_cursor_execute", fail_session)
        assert db.scalar(select(func.count()).select_from(UserModel)) == 0
        assert db.scalar(select(GuestAccessCodeModel)).consumed_at is None
        assert db.get(GuestTrialModel, fingerprint(EMAIL, KEY)).redeemed_at is None
        assert redeem(auth, code).user.id is not None


def test_operator_reset_preview_does_not_change_eligibility(engine, monkeypatch, capsys):
    from my_agents.settings import get_settings
    from scripts.guest_trial_policy import main

    with Session(engine) as db:
        user = redeem(service(db), issue(service(db))).user
        user_id = user.id
        monkeypatch.setenv("MY_AGENTS_DATABASE_URL", str(engine.url))
        get_settings.cache_clear()
        assert main(["reset", "--email", EMAIL]) == 0
        assert '"dry_run": true' in capsys.readouterr().out
        db.expire_all()
        assert db.get(GuestTrialModel, fingerprint(EMAIL, KEY)).generation == 0
        assert main(["reset", "--email", EMAIL, "--apply"]) == 0
        assert EMAIL not in capsys.readouterr().out
        db.expire_all()
        assert db.get(GuestTrialModel, fingerprint(EMAIL, KEY)).generation == 1
        assert db.get(UserModel, user_id) is not None
