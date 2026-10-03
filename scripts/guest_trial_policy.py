"""Backfill guest eligibility or explicitly reset one email's trial; dry-run by default."""

from __future__ import annotations

import argparse
import json

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select

from my_agents.auth.guest_policy import fingerprint, initialize_guest_policy, reset_trial
from my_agents.auth.models import GuestTrialModel
from my_agents.persistence.database import _sessionmaker_for_url, initialize_database
from my_agents.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backfill", "reset"])
    parser.add_argument("--email", help="Required for reset; never written to command output")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "reset" and not args.email:
        parser.error("reset requires --email")
    settings = get_settings()
    key = (
        settings.guest_cleanup_email_hmac_key.get_secret_value()
        if settings.guest_cleanup_email_hmac_key
        else ""
    )
    fingerprint("key-validation", key)
    initialize_database(settings)
    with _sessionmaker_for_url(settings.database_url)() as db:
        initialize_guest_policy(db, key)
        if args.action == "reset":
            email = str(TypeAdapter(EmailStr).validate_python(args.email)).strip().casefold()
            trial = db.scalar(
                select(GuestTrialModel).where(
                    GuestTrialModel.email_fingerprint == fingerprint(email, key)
                )
            )
            report = {
                "trial_found": trial is not None,
                "previously_redeemed": trial is not None and trial.redeemed_at is not None,
            }
            if args.apply:
                reset_trial(db, email=email, key=key)
            else:
                db.rollback()
        else:
            report = {
                "identities": len(db.scalars(select(GuestTrialModel.email_fingerprint)).all())
            }
            if args.apply:
                db.commit()
            else:
                db.rollback()
    print(json.dumps({"dry_run": not args.apply, "action": args.action, **report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
