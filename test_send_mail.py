#!/usr/bin/env python3
"""
Send a test email through iCloud SMTP (same as job_scout.send_email).

Environment variables:
  ICLOUD_EMAIL           — Apple ID / SMTP login (also used as From)
  APP_SPECIFIC_PASSWORD  — App-specific password (appleid.apple.com)

Recipient is not read from the environment. Use --to, or omit it to use
recipient_email from Job Scout config in MongoDB (same as production).

If a .env file exists next to this script, keys are loaded when not already set in the environment.

Usage:
  export ICLOUD_EMAIL='you@icloud.com'
  export APP_SPECIFIC_PASSWORD='xxxx-xxxx-xxxx-xxxx'
  python test_send_mail.py --to other@example.com

  # Uses recipient_email from Mongo (requires MONGO_URI / same DB as the app):
  python test_send_mail.py
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

ICLOUD_SMTP_HOST = "smtp.mail.me.com"
ICLOUD_SMTP_PORT = 587


def try_load_dotenv() -> None:
    """Load .env into os.environ for keys not already set (no extra dependency)."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        os.environ[key] = val


def recipient_from_mongo() -> str | None:
    try:
        import job_scout

        email = os.environ.get("TEST_USER_EMAIL", "eshansingh2409@gmail.com")
        config = job_scout.load_config(email)
        return (config.get("recipient_email") or "").strip() if config else None
    except Exception as e:
        print(f"Could not load recipient_email from MongoDB: {e}", file=sys.stderr)
        return None


def build_message(
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    attachment_path: Path | None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment_path is not None:
        path = attachment_path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"attachment not found: {path}")
        ctype, _ = mimetypes.guess_type(path.name)
        if ctype is None or "/" not in ctype:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
    return msg


def send_via_icloud(login: str, app_password: str, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    to_addr = msg["To"]
    with smtplib.SMTP(ICLOUD_SMTP_HOST, ICLOUD_SMTP_PORT, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(login, app_password)
        smtp.send_message(msg, from_addr=login, to_addrs=[to_addr])


def main() -> int:
    try_load_dotenv()

    p = argparse.ArgumentParser(
        description="Send mail through iCloud SMTP (Job Scout settings).",
    )
    p.add_argument(
        "--to",
        dest="to_addr",
        default="",
        help="Recipient. If omitted, uses recipient_email from Job Scout config (MongoDB).",
    )
    p.add_argument(
        "--subject",
        default="Job Scout — test email",
        help="Subject line.",
    )
    p.add_argument(
        "--body",
        default=(
            "This is a test message from test_send_mail.py.\n"
            "If you received this, iCloud SMTP auth is working."
        ),
        help="Plain text body.",
    )
    p.add_argument("--body-file", type=Path, help="Read plain text body from file.")
    p.add_argument("--attach", type=Path, help="Optional file attachment.")
    p.add_argument(
        "--login-email",
        dest="login_email",
        default=os.environ.get("ICLOUD_EMAIL", "").strip() or None,
        help="SMTP login. Default: ICLOUD_EMAIL.",
    )
    p.add_argument(
        "--app-password",
        dest="app_password",
        default=os.environ.get("APP_SPECIFIC_PASSWORD", "").strip() or None,
        help="App-specific password. Default: APP_SPECIFIC_PASSWORD.",
    )
    args = p.parse_args()

    to_addr = (args.to_addr or "").strip() or recipient_from_mongo()
    if not to_addr:
        print(
            "Missing recipient: pass --to or set recipient_email in the Job Scout dashboard (MongoDB).",
            file=sys.stderr,
        )
        return 2

    if not args.login_email or not args.app_password:
        print(
            "Missing credentials: set ICLOUD_EMAIL and APP_SPECIFIC_PASSWORD "
            "or pass --login-email and --app-password.",
            file=sys.stderr,
        )
        return 2

    if args.body_file:
        body = args.body_file.read_text(encoding="utf-8", errors="replace")
    else:
        body = args.body

    print(f"SMTP: {ICLOUD_SMTP_HOST}:{ICLOUD_SMTP_PORT} (STARTTLS)")
    print(f"Login / From: {args.login_email}")
    print(f"To: {to_addr}")
    print("Logging in…")

    try:
        msg = build_message(
            args.login_email,
            to_addr,
            args.subject,
            body,
            args.attach,
        )
        send_via_icloud(args.login_email, args.app_password, msg)
    except smtplib.SMTPAuthenticationError as e:
        print(f"\nSMTP authentication failed: {e}", file=sys.stderr)
        print(
            "\nCheck: APP_SPECIFIC_PASSWORD is an app-specific password (not your Apple ID password), "
            "no stray spaces, and ICLOUD_EMAIL matches the account that created the password.",
            file=sys.stderr,
        )
        return 1
    except OSError as e:
        print(f"\nNetwork / connection error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Send failed: {e}", file=sys.stderr)
        return 1

    print(f"Sent to {to_addr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
