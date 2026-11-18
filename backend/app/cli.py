"""Admin commands for standalone mode.

    python -m app.cli seed-demo-users              # one demo user per role, password from PRA_DEMO_PASSWORD
    python -m app.cli seed-demo-users --reset-passwords   # after changing PRA_DEMO_PASSWORD
    python -m app.cli set-password --email a@b.com
    python -m app.cli seed-reference-data [--tenant demo]   # demo portfolios, markets, product families
    python -m app.cli seed-artifacts [--tenant demo]        # TELCO-CANONICAL@1.0, lookups, transforms
    python -m app.cli relay-outbox                          # publish pending domain events once
    python -m app.cli create-user --email a@b.com --name "A. Patel" --role portfolio_manager [--tenant demo]
    python -m app.cli set-active --email a@b.com --inactive
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.adapters.local.audit import DbAuditLog
from app.adapters.local.events import LogEventPublisher
from app.adapters.registry import build_platform
from app.config import get_settings
from app.db.models import AppUser
from app.db.session import make_sessionmaker
from app.domain.permissions import PERSONAS
from app.services.artifact_seed import seed_artifacts
from app.services.outbox import relay_pending
from app.services.reference import seed_reference_data
from app.services.users import DEMO_USERS, UserError, create_user, normalise_email, set_password


def _session():
    return make_sessionmaker(get_settings().database_url)()


def seed_demo_users(args: argparse.Namespace) -> int:
    password = get_settings().demo_password
    if not password:
        print("Set PRA_DEMO_PASSWORD in .env first.", file=sys.stderr)
        return 1
    with _session() as s:
        try:
            for d in DEMO_USERS:
                existing = s.scalar(select(AppUser).where(AppUser.email == d.email))
                if existing is None:
                    create_user(s, email=d.email, name=d.name, password=password, roles=[d.role],
                                tenant_id="demo")
                    status = "created"
                elif args.reset_passwords:
                    set_password(existing, password)
                    status = "reset"
                else:
                    status = "exists"
                print(f"{status:<8} {d.email:<26} {PERSONAS[d.role][0]}")
        except UserError as exc:
            print(f"PRA_DEMO_PASSWORD: {exc}", file=sys.stderr)
            return 1
        s.commit()
    if not args.reset_passwords:
        print("Existing users keep their password; add --reset-passwords to apply PRA_DEMO_PASSWORD to them.")
    return 0


def _end_sessions(user: AppUser) -> None:
    """SEC-03: tokens issued before now stop working (needs Redis; skipped when it is not reachable)."""
    try:
        platform = build_platform(get_settings())
        if platform.token_issuer is not None:
            platform.token_issuer.invalidate_user(str(user.id))
    except Exception as exc:  # noqa: BLE001 - the CLI must still report the change it made
        print(f"warning: could not end the user's sessions ({type(exc).__name__}); they end at token expiry",
              file=sys.stderr)


def set_password_cmd(args: argparse.Namespace) -> int:
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    with _session() as s:
        user = s.scalar(select(AppUser).where(AppUser.email == normalise_email(args.email)))
        if user is None:
            print("No such user.", file=sys.stderr)
            return 1
        try:
            set_password(user, password)
        except UserError as exc:
            print(exc, file=sys.stderr)
            return 1
        s.commit()
        _end_sessions(user)
        print(f"Password changed for {user.email} (lockout cleared, sessions ended)")
    return 0


def seed_reference_cmd(args: argparse.Namespace) -> int:
    with _session() as s:
        created = seed_reference_data(s, args.tenant)
        s.commit()
    print(f"tenant {args.tenant}: " + ", ".join(f"{v} {k} created" for k, v in created.items()))
    return 0


def seed_artifacts_cmd(args: argparse.Namespace) -> int:
    with _session() as s:
        result = seed_artifacts(s, DbAuditLog(), args.tenant)
    for ref in result["created"]:
        print(f"published {ref}")
    for ref in result["upgraded"]:
        print(f"upgraded  {ref}")
    for key in result["existing"]:
        print(f"exists    {key}")
    return 0


def relay_outbox_cmd(args: argparse.Namespace) -> int:
    with _session() as s:
        result = relay_pending(s, LogEventPublisher(), batch=args.batch)
    print(f"published {result.published}, failed {result.failed}")
    return 0


def create_user_cmd(args: argparse.Namespace) -> int:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    with _session() as s:
        try:
            user = create_user(s, email=args.email, name=args.name, password=password,
                               roles=args.role, tenant_id=args.tenant)
        except UserError as exc:
            print(exc, file=sys.stderr)
            return 1
        s.commit()
        print(f"created {user.email} ({', '.join(user.roles)}) in tenant {user.tenant_id}")
    return 0


def set_active(args: argparse.Namespace) -> int:
    with _session() as s:
        user = s.scalar(select(AppUser).where(AppUser.email == normalise_email(args.email)))
        if user is None:
            print("No such user.", file=sys.stderr)
            return 1
        user.is_active = not args.inactive
        s.commit()
        if not user.is_active:
            _end_sessions(user)
        print(f"{user.email} is now {'active' if user.is_active else 'inactive'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("seed-demo-users", help="Create one demo user per role (tenant 'demo')")
    p.add_argument("--reset-passwords", action="store_true",
                   help="Also set existing demo users' password to PRA_DEMO_PASSWORD and unlock them")
    p.set_defaults(func=seed_demo_users)

    p = sub.add_parser("seed-reference-data",
                       help="Create demo portfolios, markets and product families for a tenant")
    p.add_argument("--tenant", default="demo")
    p.set_defaults(func=seed_reference_cmd)

    p = sub.add_parser("seed-artifacts",
                       help="Publish TELCO-CANONICAL@1.0 and the starter lookups/transforms for a tenant")
    p.add_argument("--tenant", default="demo")
    p.set_defaults(func=seed_artifacts_cmd)

    p = sub.add_parser("relay-outbox", help="Publish pending domain events once (the worker does this continuously)")
    p.add_argument("--batch", type=int, default=500)
    p.set_defaults(func=relay_outbox_cmd)

    p = sub.add_parser("set-password", help="Change a user's password (asks for it) and clear lockout")
    p.add_argument("--email", required=True)
    p.set_defaults(func=set_password_cmd)

    p = sub.add_parser("create-user", help="Create a user (asks for the password)")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", required=True, action="append", choices=list(PERSONAS),
                   help="Repeat for several roles")
    p.add_argument("--tenant", default="demo")
    p.set_defaults(func=create_user_cmd)

    p = sub.add_parser("set-active", help="Activate or deactivate a user")
    p.add_argument("--email", required=True)
    p.add_argument("--inactive", action="store_true")
    p.set_defaults(func=set_active)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
