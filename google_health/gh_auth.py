#!/usr/bin/env python3
"""Google Health API OAuth - loopback flow, no external deps beyond `requests`.

One-time: opens your browser, you approve, the token is cached to token.json.
Afterwards every other script just calls get_token() and gets a fresh access token
(refreshed automatically).

Setup expected (see README.md in this folder):
  - credentials.json downloaded from Google Cloud Console (Web application client)
  - http://localhost:8765/ registered as an Authorized redirect URI
  - publishing status "Testing", your account added under Test users
  - scope googlehealth.activity_and_fitness.readonly added on the Data Access page
"""
import json, secrets, sys, threading, time, urllib.parse, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CREDS = HERE / "credentials.json"
TOKEN = HERE / "token.json"

REDIRECT = "http://localhost:8765/"
PORT = 8765
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]


def _load_client():
    if not CREDS.exists():
        sys.exit(f"missing {CREDS}\n"
                 "Download the OAuth client JSON from Google Cloud Console and save it there.")
    d = json.loads(CREDS.read_text())
    node = d.get("web") or d.get("installed")
    if not node:
        sys.exit("credentials.json has neither a 'web' nor 'installed' section")
    # Google's Health API setup wizard refuses http:// redirect URIs, so most people end up
    # with https://www.google.com. Honour whatever is actually registered on the client.
    uris = node.get("redirect_uris") or []
    redirect = REDIRECT if REDIRECT in uris else (uris[0] if uris else REDIRECT)
    return node["client_id"], node["client_secret"], redirect


class _Handler(BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Handler.result.update({k: v[0] for k, v in q.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in q
        self.wfile.write(
            (f"<html><body style='font-family:system-ui;padding:3rem'>"
             f"<h2>{'Authorized - you can close this tab.' if ok else 'Authorization failed.'}</h2>"
             f"<p>{q.get('error', [''])[0]}</p></body></html>").encode())

    def log_message(self, *a):
        pass


def _manual_code_flow(url, redirect):
    """Used when the registered redirect URI is not a loopback address.

    Google bounces the browser to <redirect>?code=...&scope=... - nothing is listening there,
    so the page just loads normally and the code sits in the address bar. Paste it back here.
    """
    print("1. Open this URL in your browser:\n")
    print(url + "\n")
    print("2. Approve access. On the 'Google hasn't verified this app' screen choose")
    print("   Advanced -> Go to ... (unsafe). It is your own project reading your own data.")
    print(f"3. You will land on {redirect} with ?code=... in the address bar.")
    print("   Copy the ENTIRE address-bar URL (or just the code) and paste it below.\n")
    webbrowser.open(url)
    raw = input("Paste the redirect URL or the code: ").strip()
    if "code=" in raw:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        code = q.get("code", [""])[0]
    else:
        code = raw
    if not code:
        sys.exit("no authorization code found in what you pasted")
    return urllib.parse.unquote(code)


def _interactive_login(client_id, client_secret, redirect=REDIRECT):
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    loopback = redirect.startswith("http://localhost") or redirect.startswith("http://127.0.0.1")
    if not loopback:
        code = _manual_code_flow(url, redirect)
        r = requests.post(TOKEN_URL, data={
            "code": code, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=30)
        if r.status_code != 200:
            sys.exit(f"token exchange failed {r.status_code}: {r.text}")
        tok = r.json()
        tok["obtained_at"] = time.time()
        TOKEN.write_text(json.dumps(tok, indent=2))
        print(f"\nsaved {TOKEN}")
        return tok

    srv = HTTPServer(("localhost", PORT), _Handler)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    print("Opening your browser to authorize...\nIf it does not open, visit:\n" + url + "\n")
    webbrowser.open(url)
    t.join(timeout=300)
    srv.server_close()
    res = _Handler.result
    if "error" in res:
        sys.exit(f"authorization failed: {res['error']}")
    if res.get("state") != state:
        sys.exit("state mismatch - aborting")
    if "code" not in res:
        sys.exit("no authorization code received (timed out?)")

    r = requests.post(TOKEN_URL, data={
        "code": res["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"token exchange failed {r.status_code}: {r.text}")
    tok = r.json()
    tok["obtained_at"] = time.time()
    TOKEN.write_text(json.dumps(tok, indent=2))
    print(f"saved {TOKEN}")
    return tok


def _refresh(tok, client_id, client_secret):
    r = requests.post(TOKEN_URL, data={
        "refresh_token": tok["refresh_token"], "client_id": client_id,
        "client_secret": client_secret, "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        print(f"refresh failed ({r.status_code}), re-authorizing...")
        return None
    new = r.json()
    tok.update(new)
    tok["obtained_at"] = time.time()
    TOKEN.write_text(json.dumps(tok, indent=2))
    return tok


def get_token():
    """Return a valid access token, refreshing or prompting for login as needed."""
    client_id, client_secret, redirect = _load_client()
    tok = json.loads(TOKEN.read_text()) if TOKEN.exists() else None
    if tok:
        age = time.time() - tok.get("obtained_at", 0)
        if age < tok.get("expires_in", 3600) - 120:
            return tok["access_token"]
        if "refresh_token" in tok:
            t = _refresh(tok, client_id, client_secret)
            if t:
                return t["access_token"]
    return _interactive_login(client_id, client_secret, redirect)["access_token"]


if __name__ == "__main__":
    t = get_token()
    print("\naccess token acquired (first 24 chars):", t[:24] + "...")
    r = requests.get("https://health.googleapis.com/v4/users/me/identity",
                     headers={"Authorization": f"Bearer {t}", "Accept": "application/json"},
                     timeout=30)
    print(f"\nGET /users/me/identity -> {r.status_code}")
    print(r.text[:800])
