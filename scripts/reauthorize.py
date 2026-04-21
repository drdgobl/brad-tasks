#!/usr/bin/env python3
"""
One-time OAuth helper to get a refresh token with calendar.readonly scope.
Run locally, then add the refresh token to GitHub Secrets.
"""

import json
import http.server
import urllib.parse
import webbrowser

# Fill these in from Google Cloud Console
CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"
REDIRECT_URI = "http://localhost:8085/callback"
SCOPES = "https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/gmail.modify"

auth_url = (
    f"https://accounts.google.com/o/oauth2/v2/auth?"
    f"client_id={CLIENT_ID}&"
    f"redirect_uri={urllib.parse.quote(REDIRECT_URI)}&"
    f"response_type=code&"
    f"scope={urllib.parse.quote(SCOPES)}&"
    f"access_type=offline&"
    f"prompt=consent"
)

print(f"\nOpening browser for authorization...\n")
webbrowser.open(auth_url)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        code = params.get("code", [None])[0]

        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No code received")
            return

        # Exchange code for tokens
        import requests
        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        tokens = resp.json()

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Done! Check terminal for tokens.</h2>")

        print("\n--- TOKENS ---")
        print(json.dumps(tokens, indent=2))
        print(f"\nRefresh token: {tokens.get('refresh_token', 'NOT RETURNED')}")
        print("\nAdd this as GOOGLE_REFRESH_TOKEN in GitHub Secrets.")
        raise KeyboardInterrupt


server = http.server.HTTPServer(("localhost", 8085), Handler)
print("Waiting for callback on http://localhost:8085 ...")

try:
    server.handle_request()
except KeyboardInterrupt:
    pass
