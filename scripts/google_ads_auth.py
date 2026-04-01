"""Generate Google Ads API refresh token via OAuth2 flow.

Run this script locally. It will open a browser for you to authorize,
then print the refresh token to set on Railway.

Usage:
    python scripts/google_ads_auth.py
"""

import json
import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import httpx

CLIENT_ID = os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:8484"
SCOPE = "https://www.googleapis.com/auth/adwords"

auth_code = None


class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = parse_qs(urlparse(self.path).query)
        auth_code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Done! You can close this tab.</h1>")

    def log_message(self, format, *args):
        pass  # Suppress request logs


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: Set GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET env vars first.")
        print("  export GOOGLE_ADS_CLIENT_ID='your-client-id'")
        print("  export GOOGLE_ADS_CLIENT_SECRET='your-client-secret'")
        return

    # Step 1: Open browser for authorization
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPE}"
        "&response_type=code"
        "&access_type=offline"
        "&prompt=consent"
    )

    print("Opening browser for Google authorization...")
    webbrowser.open(auth_url)

    # Step 2: Wait for redirect with auth code
    server = HTTPServer(("localhost", 8484), OAuthHandler)
    server.handle_request()

    if not auth_code:
        print("ERROR: No authorization code received.")
        return

    print("Authorization code received. Exchanging for refresh token...")

    # Step 3: Exchange auth code for refresh token
    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": auth_code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )

    if response.status_code != 200:
        print(f"ERROR: Token exchange failed: {response.text}")
        return

    tokens = response.json()
    refresh_token = tokens.get("refresh_token")

    if refresh_token:
        print("\n" + "=" * 60)
        print("SUCCESS! Set this on Railway:")
        print(f"\n  GOOGLE_ADS_REFRESH_TOKEN={refresh_token}")
        print("\n" + "=" * 60)
        print("\nRun:")
        print(f'  railway variables set GOOGLE_ADS_REFRESH_TOKEN="{refresh_token}"')
    else:
        print(f"ERROR: No refresh token in response: {json.dumps(tokens, indent=2)}")


if __name__ == "__main__":
    main()
