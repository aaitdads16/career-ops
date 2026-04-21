#!/usr/bin/env python3
"""
gmail_setup.py — One-time Gmail OAuth2 setup.

Run this ONCE on your local machine:
    python3 gmail_setup.py

What it does:
  1. Opens your browser for Google OAuth consent
  2. Asks you to authorize read-only Gmail access
  3. Saves credentials to gmail_token.json
  4. Prints the JSON to copy into GitHub Secret GMAIL_TOKEN_JSON

Requirements:
    pip install google-auth-oauthlib google-api-python-client

You also need a Google Cloud Project with Gmail API enabled:
  1. Go to https://console.cloud.google.com/
  2. New project → Enable Gmail API
  3. OAuth consent screen → External → Add your email as test user
  4. Credentials → Create → OAuth client ID → Desktop app
  5. Download JSON → save as 'gmail_client_secrets.json' next to this script
"""

import json
import sys
from pathlib import Path

SECRETS_FILE = Path(__file__).parent / "gmail_client_secrets.json"
TOKEN_FILE   = Path(__file__).parent / "gmail_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",  # needed to mark emails as read
]


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("ERROR: Missing Google libraries.")
        print("Install with: pip install google-auth-oauthlib google-api-python-client google-auth-httplib2")
        sys.exit(1)

    if not SECRETS_FILE.exists():
        print(f"\nERROR: {SECRETS_FILE} not found.")
        print("\nSteps to create it:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create a project → Enable Gmail API")
        print("  3. OAuth consent screen → External → Add your email as test user")
        print("  4. Credentials → Create OAuth client ID → Desktop app")
        print("  5. Download JSON → save as 'gmail_client_secrets.json' in career-ops/")
        sys.exit(1)

    print("Starting Gmail OAuth flow...")
    print("A browser window will open — sign in and grant access.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(creds.to_json())
    print(f"\n✅ Credentials saved to {TOKEN_FILE}")
    print("\n" + "="*60)
    print("NEXT STEP — Add to GitHub Secrets:")
    print("  Secret name:  GMAIL_TOKEN_JSON")
    print("  Secret value: (copy everything below this line)")
    print("="*60)
    print(TOKEN_FILE.read_text())
    print("="*60)
    print("\nAlso make sure GMAIL_ENABLED=true is set in GitHub secrets.")
    print("Done! Gmail integration is ready.")


if __name__ == "__main__":
    main()
