"""
Instagram One-Time Login Helper
================================
Run this script ONCE on the same machine that will run the scheduler.
It performs an interactive login (handling SMS / email verification
challenges that Instagram throws up for new automated logins) and saves
the resulting session to `ig_session.json`.

After this completes successfully, the scheduled run in news_automation.py
will reuse the session and won't trigger a fresh login (which is what
gets accounts/IPs flagged).

Usage:
    python ig_login.py

Re-run any time you see "challenge_required", "login_required", or
the "your IP is on the blacklist" error in automation.log.
"""

import os
import sys
import getpass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

USERNAME = os.getenv("INSTAGRAM_USERNAME")
PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
SESSION_FILE = Path(os.getenv("INSTAGRAM_SESSION_FILE", "ig_session.json"))
PROXY = os.getenv("INSTAGRAM_PROXY") or None


def _challenge_handler(username: str, choice):
    """
    Called by instagrapi when Instagram requires a verification code.
    `choice` is the channel (SMS / email). We just prompt the human at the
    terminal to type in the 6-digit code they receive.
    """
    print(f"\n[!] Instagram is asking for a verification code for @{username}.")
    print(f"    Method: {choice}")
    code = input("    Enter the 6-digit code: ").strip()
    return code


def _change_password_handler(username: str):
    """If Instagram demands a password change, prompt the user."""
    print(f"\n[!] Instagram requires a password change for @{username}.")
    new_pw = getpass.getpass("    Enter a new password: ")
    return new_pw


def main():
    if not USERNAME or not PASSWORD:
        print("ERROR: INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD missing in .env")
        sys.exit(1)

    try:
        from instagrapi import Client
    except ImportError:
        print("ERROR: instagrapi not installed. Run: pip install instagrapi")
        sys.exit(1)

    print(f"Logging in as @{USERNAME}...")
    if PROXY:
        print(f"  via proxy {PROXY.split('@')[-1]}")

    cl = Client()
    cl.delay_range = [2, 5]
    cl.challenge_code_handler = _challenge_handler
    cl.change_password_handler = _change_password_handler

    if PROXY:
        cl.set_proxy(PROXY)

    # If a session file already exists, try it first — sometimes it's still
    # valid and we just need to re-confirm.
    if SESSION_FILE.exists():
        try:
            cl.load_settings(SESSION_FILE)
            cl.login(USERNAME, PASSWORD)
            cl.get_timeline_feed()
            print(f"✅ Existing session at {SESSION_FILE} is still valid. Nothing to do.")
            return
        except Exception as e:
            print(f"  Existing session unusable ({e}). Doing fresh login...")
            cl = Client()
            cl.delay_range = [2, 5]
            cl.challenge_code_handler = _challenge_handler
            cl.change_password_handler = _change_password_handler
            if PROXY:
                cl.set_proxy(PROXY)

    try:
        cl.login(USERNAME, PASSWORD)
    except Exception as e:
        print(f"❌ Login failed: {e}")
        print("\nIf this is an IP blacklist or 'feedback_required' error:")
        print("  1. Open Instagram on your phone, log in, and complete any 'Was this you?' prompt.")
        print("  2. Wait 24–48 hours before retrying from this machine.")
        print("  3. Or set INSTAGRAM_PROXY in .env to route through a different IP.")
        sys.exit(1)

    cl.dump_settings(SESSION_FILE)
    print(f"✅ Logged in. Session saved to {SESSION_FILE.resolve()}")
    print("   The scheduled run will now reuse this session — no more fresh logins.")


if __name__ == "__main__":
    main()
