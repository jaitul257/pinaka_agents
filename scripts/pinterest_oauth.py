"""One-shot Pinterest OAuth2 token generator.

Pinterest's quick-generate button only gives read scopes. To get
`pins:write` on Trial Access, we need to run the full OAuth2
authorization_code flow. This script does that locally:

  1. Spins up a tiny HTTP server on localhost:8765
  2. Prints the authorize URL
  3. You open the URL in a browser, sign in, approve
  4. Pinterest redirects to localhost:8765/callback?code=<code>
  5. Script exchanges the code for an access_token
  6. Token + refresh_token printed to stdout

REQUIREMENTS on the Pinterest app:
  - Redirect URI: http://localhost:8765/callback (add in dev portal)
  - App must be in Trial Access (default — no review needed)
  - APP_ID + APP_SECRET below must match the app

USAGE:
  PINTEREST_APP_ID=... PINTEREST_APP_SECRET=... \
    .venv/bin/python scripts/pinterest_oauth.py
"""

from __future__ import annotations

import base64
import http.server
import os
import socketserver
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

APP_ID = os.environ.get("PINTEREST_APP_ID", "").strip()
APP_SECRET = os.environ.get("PINTEREST_APP_SECRET", "").strip()
REDIRECT_URI = "http://localhost:8765/callback"
# Pinterest v5 requires more scopes than the obvious minimum:
# POST /v5/pins returns a 401 "Missing: ['boards:write', 'pins:read']"
# even though creating a pin logically shouldn't need boards:write.
# Safest: request all pin+board read+write scopes.
SCOPES = "pins:read,pins:write,boards:read,boards:write,user_accounts:read"

if not APP_ID or not APP_SECRET:
    print("ERROR: set PINTEREST_APP_ID and PINTEREST_APP_SECRET env vars first.")
    print("  export PINTEREST_APP_ID=1562625")
    print("  export PINTEREST_APP_SECRET=f64b954c36c21460f6f673e08d6d58516645968a")
    sys.exit(1)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Receive Pinterest's redirect, extract `code`, print & exit."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        code = (params.get("code") or [""])[0]
        error = (params.get("error") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if error:
            body = (f"<h1>Pinterest OAuth failed</h1><p>{error}</p>"
                    f"<p>Return to your terminal for details.</p>").encode()
            self.server.result = {"error": error}  # type: ignore
        elif code:
            body = ("<h1>Got the code - you can close this tab.</h1>"
                    "<p>Return to your terminal to see the access token.</p>"
                   ).encode("utf-8")
            self.server.result = {"code": code}  # type: ignore
        else:
            body = b"<h1>No code received</h1>"
            self.server.result = {"error": "no_code"}  # type: ignore
        self.wfile.write(body)

    def log_message(self, *_args, **_kwargs):
        return  # silence default stderr logging


def exchange_code_for_token(code: str) -> dict:
    """POST to /v5/oauth/token with client credentials + code.

    Pinterest uses a Basic auth header — `Authorization: Basic base64(id:secret)`.
    """
    basic = base64.b64encode(f"{APP_ID}:{APP_SECRET}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        "https://api.pinterest.com/v5/oauth/token",
        data=data,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            import json
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"HTTP {e.code}", "detail": body}


def main() -> int:
    # Start the callback server in a thread
    server = socketserver.TCPServer(("localhost", 8765), _CallbackHandler)
    server.result = None  # type: ignore
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Build + open the authorize URL
    auth_params = {
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "response_type": "code",
        "state": "pinaka-local-setup",
    }
    auth_url = (
        "https://www.pinterest.com/oauth/?"
        + urllib.parse.urlencode(auth_params)
    )
    print("=" * 72)
    print("Pinterest OAuth — one-shot token generator")
    print("=" * 72)
    print()
    print("Requested scopes: " + SCOPES)
    print()
    print("Opening your browser. If nothing opens, paste this URL manually:")
    print()
    print(auth_url)
    print()
    print("Waiting for callback on http://localhost:8765/callback ...")
    print("(If you see 'Redirect URI mismatch', add that exact URI to your")
    print(" Pinterest app in dev portal → Redirect URIs.)")
    print()

    webbrowser.open(auth_url)

    # Poll for result
    import time
    for _ in range(300):  # 5-minute wait
        if getattr(server, "result", None):
            break
        time.sleep(1)
    server.shutdown()

    result = getattr(server, "result", None)
    if not result:
        print("TIMEOUT — no callback received in 5 minutes.")
        return 2
    if "error" in result:
        print(f"OAuth flow returned error: {result['error']}")
        return 3

    code = result["code"]
    print(f"Got authorization code: {code[:20]}...")
    print("Exchanging for access token...")
    token_response = exchange_code_for_token(code)

    if "access_token" not in token_response:
        print("Token exchange FAILED:")
        print(token_response)
        return 4

    access = token_response["access_token"]
    refresh = token_response.get("refresh_token", "")
    scope = token_response.get("scope", "")
    expires_in = token_response.get("expires_in", "")

    print()
    print("=" * 72)
    print("SUCCESS — token details")
    print("=" * 72)
    print(f"  scope:          {scope}")
    print(f"  expires_in:     {expires_in}s  (~{int(expires_in)//86400 if expires_in else 0}d)")
    print()
    print("Set on Railway:")
    print(f'  railway variables --set "PINTEREST_ACCESS_TOKEN={access}"')
    if refresh:
        print(f'  railway variables --set "PINTEREST_REFRESH_TOKEN={refresh}"')
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
