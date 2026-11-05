import json
import tempfile
import unittest
from pathlib import Path

from t2l.trakt import API, AUTH, AuthError, TraktClient, load_creds, save_creds


class FakeHTTP:
    """Scripted transport: each call pops the next (status, headers, body) and records the request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.slept = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, json.loads(body) if body else None))
        status, hdrs, payload = self.responses.pop(0)
        return status, hdrs, json.dumps(payload).encode()

    def sleep(self, s):
        self.slept.append(s)


def client(responses, now=1_000_000, **creds):
    http = FakeHTTP(responses)
    base = {"client_id": "cid", "client_secret": "sec", "access_token": "at",
            "refresh_token": "rt", "expires_at": now + 86400}
    base.update(creds)
    return TraktClient(base, request=http, sleep=http.sleep, now=lambda: now), http


class HeadersTest(unittest.TestCase):
    def test_get_sends_required_headers(self):
        c, http = client([(200, {}, {"movies": {}})])
        c.last_activities()
        method, url, headers, _ = http.calls[0]
        self.assertEqual((method, url), ("GET", API + "/sync/last_activities"))
        self.assertEqual(headers["trakt-api-version"], "2")
        self.assertEqual(headers["trakt-api-key"], "cid")
        self.assertEqual(headers["Authorization"], "Bearer at")
        self.assertEqual(headers["User-Agent"], "trakt-to-letterboxd/0.1")
        self.assertEqual(headers["Content-Type"], "application/json")


class RefreshTest(unittest.TestCase):
    def test_skips_refresh_when_token_fresh(self):
        c, http = client([])
        c.ensure_token()
        self.assertEqual(http.calls, [])
        self.assertFalse(c.creds_changed)

    def test_refreshes_when_expiring_within_5_minutes(self):
        c, http = client([(200, {}, {"access_token": "new", "refresh_token": "newr",
                                     "expires_in": 86400, "created_at": 1_000_000})],
                         expires_at=1_000_000 + 200)
        c.ensure_token()
        _, url, _, body = http.calls[0]
        self.assertEqual(url, AUTH + "/oauth/token")
        self.assertEqual(body["grant_type"], "refresh_token")
        self.assertEqual(body["refresh_token"], "rt")
        self.assertEqual(body["redirect_uri"], "urn:ietf:wg:oauth:2.0:oob")
        self.assertEqual(c.creds["access_token"], "new")
        self.assertEqual(c.creds["refresh_token"], "newr")
        self.assertEqual(c.creds["expires_at"], 1_000_000 + 86400)
        self.assertTrue(c.creds_changed)

    def test_invalid_grant_raises_auth_error(self):
        c, _ = client([(400, {}, {"error": "invalid_grant"})], expires_at=0)
        with self.assertRaises(AuthError) as ctx:
            c.ensure_token()
        self.assertIn("auth", str(ctx.exception))


class DeviceFlowTest(unittest.TestCase):
    def test_polls_until_approved(self):
        c, http = client([(400, {}, {}), (200, {}, {"access_token": "a", "refresh_token": "r",
                                                   "expires_in": 100, "created_at": 5})])
        c.wait_for_device_token({"device_code": "dc", "interval": 5, "expires_in": 600})
        self.assertEqual(http.slept, [5, 5])
        self.assertEqual(http.calls[0][3], {"code": "dc", "client_id": "cid", "client_secret": "sec"})
        self.assertEqual(c.creds["expires_at"], 105)

    def test_expired_code_raises(self):
        c, _ = client([(410, {}, {})])
        with self.assertRaises(AuthError):
            c.wait_for_device_token({"device_code": "dc", "interval": 1, "expires_in": 600})


class PagingAndRateLimitTest(unittest.TestCase):
    def test_get_all_follows_page_count(self):
        c, http = client([(200, {"x-pagination-page-count": "2"}, [{"id": 1}]),
                          (200, {"x-pagination-page-count": "2"}, [{"id": 2}])])
        items = c.history_movies("2026-01-01T00:00:00Z")
        self.assertEqual([i["id"] for i in items], [1, 2])
        self.assertIn("page=1", http.calls[0][1])
        self.assertIn("limit=250", http.calls[0][1])
        self.assertIn("start_at=2026-01-01T00%3A00%3A00Z", http.calls[0][1])
        self.assertIn("page=2", http.calls[1][1])

    def test_history_without_start_at_omits_param(self):
        c, http = client([(200, {"x-pagination-page-count": "1"}, [])])
        c.history_movies(None)
        self.assertNotIn("start_at", http.calls[0][1])

    def test_429_waits_retry_after_and_retries_once(self):
        c, http = client([(429, {"retry-after": "3"}, {}), (200, {}, {"movies": {}})])
        c.last_activities()
        self.assertEqual(http.slept, [3])
        self.assertEqual(len(http.calls), 2)

    def test_ratings_map(self):
        c, _ = client([(200, {"x-pagination-page-count": "1"},
                        [{"rating": 8, "movie": {"ids": {"trakt": 1}}}, {"rating": 5, "movie": {"ids": {"trakt": 2}}}])])
        self.assertEqual(c.ratings_movies(), {1: 8, 2: 5})


class CredsFileTest(unittest.TestCase):
    def test_round_trip_with_0600(self):
        path = Path(tempfile.mkdtemp()) / "creds.json"
        save_creds(path, {"client_id": "x"})
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(load_creds(path), {"client_id": "x"})


if __name__ == "__main__":
    unittest.main()
