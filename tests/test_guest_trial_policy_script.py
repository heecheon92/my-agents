"""Guest reset CLI preserves explicit env targeting and the standalone process-env default."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from my_agents.auth.guest_policy import fingerprint, initialize_guest_policy
from my_agents.auth.models import GuestTrialModel
from my_agents.persistence.database import Base
from my_agents.persistence.models import import_all_models
from my_agents.settings import Settings
from scripts import guest_trial_policy, ops

KEY = "synthetic-script-test-key-at-least-32-bytes"
EMAIL = "guest@example.com"


@pytest.mark.parametrize("use_profile", [False, True])
def test_reset_uses_selected_env_without_resetting_the_process_database(
    tmp_path, monkeypatch, use_profile
):
    monkeypatch.delenv("MY_AGENTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY", raising=False)
    import_all_models()
    urls = [f"sqlite+pysqlite:///{tmp_path / name}" for name in ["selected.db", "process.db"]]
    engines = [create_engine(url) for url in urls]
    for engine in engines:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            initialize_guest_policy(db, KEY)
            db.add(
                GuestTrialModel(
                    email_fingerprint=fingerprint(EMAIL, KEY),
                    redeemed_at=datetime.now(UTC),
                    generation=0,
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()
    try:
        # A previously cached process configuration must not override explicit CLI targeting.
        process_settings = Settings(
            _env_file=None,
            MY_AGENTS_DATABASE_URL=urls[1],
            MY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY=KEY,
        )
        monkeypatch.setattr(guest_trial_policy, "get_settings", lambda: process_settings)
        env_file = tmp_path / (".env.pgvector.production" if use_profile else "selected.env")
        env_file.write_text(
            f"MY_AGENTS_DATABASE_URL={urls[0]}\nMY_AGENTS_GUEST_CLEANUP_EMAIL_HMAC_KEY={KEY}\n"
        )
        monkeypatch.chdir(tmp_path)
        env_args = (
            ["--env", "pgvector.production"] if use_profile else ["--env-file", str(env_file)]
        )
        command = [*env_args, "guest", "reset", "--email", EMAIL]
        assert ops.main(command) == 0
        with Session(engines[0]) as db:
            assert db.get(GuestTrialModel, fingerprint(EMAIL, KEY)).generation == 0
        assert ops.main([*command, "--apply"]) == 0
        with Session(engines[0]) as db:
            trial = db.get(GuestTrialModel, fingerprint(EMAIL, KEY))
            assert trial.generation == 1 and trial.redeemed_at is None
        with Session(engines[1]) as db:
            assert db.get(GuestTrialModel, fingerprint(EMAIL, KEY)).generation == 0
        # Existing standalone usage (including Render Shell) still needs no env-file argument.
        assert guest_trial_policy.main(["reset", "--email", EMAIL, "--apply"]) == 0
        with Session(engines[1]) as db:
            assert db.get(GuestTrialModel, fingerprint(EMAIL, KEY)).generation == 1
    finally:
        for engine in engines:
            engine.dispose()


def test_missing_explicit_env_file_does_not_fall_back_to_process_settings(tmp_path, monkeypatch):
    def unexpected_settings():
        raise AssertionError("must not silently reset against the process environment")

    monkeypatch.setattr(guest_trial_policy, "get_settings", unexpected_settings)
    assert (
        guest_trial_policy.main(
            ["--env-file", str(tmp_path / "missing.env"), "reset", "--email", EMAIL, "--apply"]
        )
        == 1
    )
