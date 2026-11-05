"""Minimal Trakt API client: device auth, token refresh, paged GETs."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH = "https://auth.trakt.tv"
API = "https://api.trakt.tv"
USER_AGENT = "trakt-to-letterboxd/0.1"
REAUTH = "run `python3 -m t2l auth` again"


class AuthError(Exception):
    """The user has to run the auth command again."""


def request(method, url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def load_creds(path):
    return json.loads(Path(path).read_text())


def save_creds(path, creds):
    path = Path(path)
    path.write_text(json.dumps(creds, indent=1))
    os.chmod(path, 0o600)


class TraktClient:
    def __init__(self, creds, request=request, sleep=time.sleep, now=time.time):
        self.creds = creds
        self._request, self._sleep, self._now = request, sleep, now
        self.creds_changed = False

    def _headers(self):
        h = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
             "trakt-api-key": self.creds["client_id"], "trakt-api-version": "2"}
        if self.creds.get("access_token"):
            h["Authorization"] = "Bearer " + self.creds["access_token"]
        return h

    def _post(self, url, payload):
        status, headers, body = self._request("POST", url, self._headers(), json.dumps(payload).encode())
        return status, json.loads(body) if body else {}

    def _store_tokens(self, tok):
        self.creds["access_token"] = tok["access_token"]
        self.creds["refresh_token"] = tok["refresh_token"]
        self.creds["expires_at"] = tok["created_at"] + tok["expires_in"]
        self.creds_changed = True

    def device_code(self):
        status, data = self._post(AUTH + "/oauth/device/code", {"client_id": self.creds["client_id"]})
        if status != 200:
            raise AuthError(f"device code request failed ({status})")
        return data

    def wait_for_device_token(self, device):
        payload = {"code": device["device_code"], "client_id": self.creds["client_id"],
                   "client_secret": self.creds["client_secret"]}
        interval, waited = device["interval"], 0
        while waited < device["expires_in"]:
            self._sleep(interval)
            waited += interval
            status, data = self._post(AUTH + "/oauth/device/token", payload)
            if status == 200:
                return self._store_tokens(data)
            if status == 429:
                interval *= 2
            elif status != 400:
                raise AuthError(f"device authorisation failed ({status}): {REAUTH}")
        raise AuthError(f"device code expired: {REAUTH}")

    def ensure_token(self):
        if self.creds.get("expires_at", 0) - self._now() > 300:
            return
        # Trakt's refresh grant still requires redirect_uri; oob is the device-flow value.
        status, data = self._post(AUTH + "/oauth/token", {
            "refresh_token": self.creds.get("refresh_token"), "client_id": self.creds["client_id"],
            "client_secret": self.creds["client_secret"], "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token"})
        if status != 200:
            raise AuthError(f"refresh failed ({status}): {REAUTH}")
        self._store_tokens(data)

    def get(self, path, params=None):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        status, headers, body = self._request("GET", url, self._headers(), None)
        if status == 429:
            self._sleep(int(headers.get("retry-after", 10)))
            status, headers, body = self._request("GET", url, self._headers(), None)
        if status != 200:
            raise RuntimeError(f"GET {path} failed ({status})")
        return json.loads(body), headers

    def get_all(self, path, params=None):
        params = dict(params or {}, limit=250, page=1)
        items, headers = self.get(path, params)
        for page in range(2, int(headers.get("x-pagination-page-count", 1)) + 1):
            more, _ = self.get(path, dict(params, page=page))
            items.extend(more)
        return items

    def last_activities(self):
        return self.get("/sync/last_activities")[0]

    def history_movies(self, start_at):
        params = {"start_at": start_at} if start_at else {}
        return self.get_all("/sync/history/movies", params)

    def ratings_movies(self):
        return {r["movie"]["ids"]["trakt"]: r["rating"] for r in self.get_all("/sync/ratings/movies")}
