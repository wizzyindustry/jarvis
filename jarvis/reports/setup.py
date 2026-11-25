"""Connect accounts. Tokens go to Windows Credential Manager.

    .venv\\Scripts\\python.exe -m jarvis.reports.setup google        # Gmail + YouTube (opens browser)
    .venv\\Scripts\\python.exe -m jarvis.reports.setup instagram     # paste a token from the Meta dashboard
    .venv\\Scripts\\python.exe -m jarvis.reports.setup facebook      # Page ID + Page token
    .venv\\Scripts\\python.exe -m jarvis.reports.setup status
    .venv\\Scripts\\python.exe -m jarvis.reports.setup disconnect google|instagram|facebook
"""
from __future__ import annotations

import getpass
import sys

from . import google, meta, secrets

NAMES = {"google": "google", "instagram": "instagram", "facebook": "facebook_page"}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "google":
        google.sign_in()
        print("Google connected. Set gmail = true and/or youtube = true in config.toml.")
    elif cmd == "instagram":
        tok = getpass.getpass("Paste the Instagram token (input hidden): ").strip()
        print(f"Instagram connected as @{meta.save_instagram_token(tok)}. Set meta = true in config.toml.")
    elif cmd == "facebook":
        page = input("Facebook Page ID: ").strip()
        tok = getpass.getpass("Paste the Page access token (input hidden): ").strip()
        print(f"Facebook Page '{meta.save_facebook_page(page, tok)}' connected. Set meta = true in config.toml.")
    elif cmd == "status":
        for label, key in NAMES.items():
            print(f"{label:10} {'connected' if secrets.get(key) else 'not connected'}")
    elif cmd == "disconnect" and len(argv) > 1 and argv[1] in NAMES:
        secrets.delete(NAMES[argv[1]])
        print(f"{argv[1]} disconnected (token removed).")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
