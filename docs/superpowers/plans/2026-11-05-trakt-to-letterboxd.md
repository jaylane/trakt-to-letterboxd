# Trakt to Letterboxd Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A launchd-scheduled, stdlib-only Python tool that polls Trakt for new movie plays, writes them as an incremental Letterboxd import CSV, and notifies the user to upload it.

**Architecture:** Three modules under `t2l/`: `export.py` turns Trakt history items into Letterboxd CSV rows and files (pure functions), `trakt.py` is a small HTTP client with device auth, token refresh, pagination and 429 handling (HTTP injected for tests), `__main__.py` holds the commands, state file and macOS notification. A launchd plist runs `python3 -m t2l run` every 15 minutes. The Letterboxd upload stays manual because every automated path breaks Letterboxd's terms.

**Tech Stack:** Python 3.9+ standard library only (`urllib`, `json`, `csv`, `datetime`, `pathlib`, `subprocess`, `unittest`). launchd. No pip dependencies.

**Spec:** `docs/superpowers/specs/2026-11-05-trakt-to-letterboxd-design.md` (research in `docs/research/2026-11-05-trakt-letterboxd-brief.md`)

## Global Constraints

- No third-party packages. Tests run with `python3 -m unittest`.
- Python 3.9 compatible: no `str | None` in runtime annotations without `from __future__ import annotations`, no `datetime.fromisoformat` on a trailing `Z`.
- CSV header exactly `tmdbID,imdbID,Title,Year,WatchedDate,Rewatch,Rating10`; inner quotes escaped with backslash; each file under 1,000,000 bytes.
- `User-Agent: trakt-to-letterboxd/0.1`, `trakt-api-version: 2` on every Trakt call.
- `creds.json` written with mode `0600`.
- Nothing in this repo talks to letterboxd.com.
- Comments say why, not what (user rule `wagmo-code-comments`).

## Review Focus

1. A title containing a double quote or comma (`"Crocodile" Dundee`, `Me, Myself & Irene`) must produce a row Letterboxd parses: quoted field, backslash before the inner quote. Test in Task 1.
2. A play at 03:00 UTC must land on the previous local calendar day in a US zone. Test in Task 1 with an explicit `tz`.
3. A movie with neither `tmdb` nor `imdb` id must still export a Title + Year row rather than crash or drop. Test in Task 1.
4. A refresh that answers 400 `invalid_grant` must exit with a one-line "run auth again" message and status 2, not a traceback, so the launchd log stays readable. Tests in Task 2 and Task 3.
5. A run with unchanged `last_activities` must make no history call and send no notification, since this is 95 of every 96 runs. Test in Task 3.

---

### Task 1: Scaffold and CSV export

**Files:**
- Create: `.gitignore`, `t2l/__init__.py`, `t2l/export.py`, `tests/__init__.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `COLUMNS = ["tmdbID", "imdbID", "Title", "Year", "WatchedDate", "Rewatch", "Rating10"]`
  - `watched_date(watched_at: str, tz: tzinfo | None = None) -> str`: Trakt UTC ISO stamp (with trailing `Z` and optional milliseconds) to `YYYY-MM-DD` in `tz`, or the system local zone when `tz` is None.
  - `build_rows(items: list[dict], ratings: dict[int, int], played: set[int], tz=None) -> list[dict]`: items are Trakt history items in any order; returns rows keyed by `COLUMNS` in ascending `watched_at` order; adds each movie's Trakt id to `played` as it goes so `Rewatch` is `"true"` from the second play onward, and `""` otherwise. Missing ids become `""`. `Year` is `str(year)` or `""`. `Rating10` is `str(ratings[trakt_id])` or `""`.
  - `write_parts(rows: list[dict], out_dir: Path, limit: int = 1_000_000) -> list[Path]`: deletes any existing `pending-*.csv` in `out_dir`, writes `pending-1.csv`, `pending-2.csv`, ... each with the header and under `limit` bytes; returns the written paths; returns `[]` and writes nothing when `rows` is empty.

- [ ] **Step 1: Initialise the repo**

```bash
git init
printf '__pycache__/\n*.pyc\n.DS_Store\n' > .gitignore
mkdir -p t2l tests && touch t2l/__init__.py tests/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`tests/test_export.py`:

```python
import csv
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path

from t2l.export import COLUMNS, build_rows, watched_date, write_parts

PST = timezone(timedelta(hours=-8))


def item(hid, watched_at, trakt=1, tmdb=603, imdb="tt0133093", title="The Matrix", year=1999):
    return {"id": hid, "watched_at": watched_at, "action": "scrobble", "type": "movie",
            "movie": {"title": title, "year": year,
                      "ids": {"trakt": trakt, "slug": "x", "imdb": imdb, "tmdb": tmdb}}}


class WatchedDateTest(unittest.TestCase):
    def test_utc_early_morning_is_previous_local_day(self):
        self.assertEqual(watched_date("2026-11-05T03:00:00.000Z", PST), "2026-11-04")

    def test_accepts_stamp_without_millis(self):
        self.assertEqual(watched_date("2026-11-05T12:00:00Z", timezone.utc), "2026-11-05")


class BuildRowsTest(unittest.TestCase):
    def test_row_shape_and_rating(self):
        rows = build_rows([item(10, "2026-11-01T20:00:00.000Z")], {1: 9}, set(), timezone.utc)
        self.assertEqual(rows, [{"tmdbID": "603", "imdbID": "tt0133093", "Title": "The Matrix",
                                 "Year": "1999", "WatchedDate": "2026-11-01", "Rewatch": "",
                                 "Rating10": "9"}])

    def test_rewatch_within_batch_and_across_runs(self):
        played = {7}
        rows = build_rows([item(2, "2026-11-02T20:00:00Z"), item(1, "2026-11-01T20:00:00Z"),
                           item(3, "2026-11-03T20:00:00Z", trakt=7, tmdb=99)], {}, played, timezone.utc)
        self.assertEqual([r["WatchedDate"] for r in rows], ["2026-11-01", "2026-11-02", "2026-11-03"])
        self.assertEqual([r["Rewatch"] for r in rows], ["", "true", "true"])
        self.assertEqual(played, {1, 7})

    def test_missing_ids_and_year(self):
        rows = build_rows([item(1, "2026-11-01T20:00:00Z", tmdb=None, imdb=None, year=None)], {}, set(), timezone.utc)
        self.assertEqual((rows[0]["tmdbID"], rows[0]["imdbID"], rows[0]["Year"]), ("", "", ""))
        self.assertEqual(rows[0]["Title"], "The Matrix")


class WritePartsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def row(self, title="The Matrix", date="2026-11-01"):
        return {"tmdbID": "603", "imdbID": "tt0133093", "Title": title, "Year": "1999",
                "WatchedDate": date, "Rewatch": "", "Rating10": ""}

    def test_header_and_backslash_escaped_quote(self):
        (path,) = write_parts([self.row(title='"Crocodile" Dundee'), self.row(title="Me, Myself & Irene")], self.dir)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(",".join(COLUMNS) + "\n"))
        self.assertIn('"\\"Crocodile\\" Dundee"', text)
        self.assertIn('"Me, Myself & Irene"', text)
        self.assertNotIn('""Crocodile', text)

    def test_splits_under_limit_with_header_each(self):
        paths = write_parts([self.row() for _ in range(50)], self.dir, limit=600)
        self.assertGreater(len(paths), 1)
        for p in paths:
            self.assertLess(p.stat().st_size, 600)
            with p.open(encoding="utf-8") as f:
                self.assertEqual(next(csv.reader(f)), COLUMNS)
        self.assertEqual(sorted(p.name for p in self.dir.glob("pending-*.csv")), sorted(p.name for p in paths))

    def test_empty_rows_removes_stale_files(self):
        (self.dir / "pending-1.csv").write_text("stale")
        self.assertEqual(write_parts([], self.dir), [])
        self.assertEqual(list(self.dir.glob("pending-*.csv")), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_export -v`
Expected: FAIL with `ModuleNotFoundError: No module named 't2l.export'`

- [ ] **Step 4: Implement `t2l/export.py`**

Signatures from the Interfaces block. Choices the tests do not pin: parse with `datetime.fromisoformat(stamp.replace("Z", "+00:00"))` then `.astimezone(tz).date().isoformat()`; sort items by `watched_at` string (ISO UTC sorts lexically). The writer uses `csv.DictWriter(f, COLUMNS, doublequote=False, escapechar="\\", lineterminator="\n")`. Measure each rendered row with `len(line.encode("utf-8"))` and start a new part when `header_bytes + part_bytes + row_bytes >= limit`. Add one comment on `doublequote=False` naming the Letterboxd rule.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_export -v`
Expected: 8 tests, `OK`

- [ ] **Step 6: Commit**

```bash
git add .gitignore t2l tests
git commit -m "feat: letterboxd csv export from trakt history items"
```

---

### Task 2: Trakt client

**Files:**
- Create: `t2l/trakt.py`, `tests/test_trakt.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `AUTH = "https://auth.trakt.tv"`, `API = "https://api.trakt.tv"`, `USER_AGENT = "trakt-to-letterboxd/0.1"`
  - `class AuthError(Exception)`: raised when the user must run `auth` again.
  - `request(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]`: the default urllib transport. Returns the status instead of raising on 4xx/5xx. Response headers dict has lower-cased keys.
  - `class TraktClient`:
    - `__init__(self, creds: dict, request=request, sleep=time.sleep, now=time.time)`. `creds` keys: `client_id, client_secret, access_token?, refresh_token?, expires_at?` (epoch seconds). The client mutates `creds` in place when tokens change and sets `self.creds_changed = True`.
    - `device_code(self) -> dict`: `POST {AUTH}/oauth/device/code` with `{client_id}`; returns the JSON (`device_code, user_code, verification_url, expires_in, interval`).
    - `wait_for_device_token(self, device: dict) -> None`: polls `POST {AUTH}/oauth/device/token` with `{code, client_id, client_secret}` every `device["interval"]` seconds via `sleep`. 200 stores tokens and returns; 400 keeps polling; 429 doubles the wait once; 410, 418, 404, 409 raise `AuthError` with the status in the message.
    - `ensure_token(self) -> None`: no-op if `expires_at - now() > 300`; otherwise `POST {AUTH}/oauth/token` with `grant_type=refresh_token`, `redirect_uri="urn:ietf:wg:oauth:2.0:oob"`; stores the new pair; any non-200 raises `AuthError("refresh failed (<status>): run `python3 -m t2l auth` again")`.
    - `get(self, path: str, params: dict | None = None) -> tuple[object, dict]`: GET `{API}{path}?{urlencode(params)}` with the five headers; on 429 sleeps `Retry-After` seconds (default 10) and retries once; any other non-200 raises `RuntimeError` with status and path; returns `(json, headers)`.
    - `get_all(self, path: str, params: dict | None = None) -> list`: pages with `page=1..X-Pagination-Page-Count`, `limit=250`, concatenating lists.
    - `last_activities(self) -> dict`, `history_movies(self, start_at: str | None) -> list` (passes `start_at` only when given), `ratings_movies(self) -> dict[int, int]` mapping `movie.ids.trakt` to `rating`.
  - `load_creds(path: Path) -> dict`, `save_creds(path: Path, creds: dict) -> None` (writes JSON, `chmod 0600`).
  - Token storage on 200 from device token or refresh: `access_token`, `refresh_token`, `expires_at = created_at + expires_in`.

- [ ] **Step 1: Write the failing tests**

`tests/test_trakt.py`:

```python
import json
import os
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_trakt -v`
Expected: FAIL with `ModuleNotFoundError: No module named 't2l.trakt'`

- [ ] **Step 3: Implement `t2l/trakt.py`**

Signatures from the Interfaces block. The default `request` uses `urllib.request.Request` with `method=`, catches `urllib.error.HTTPError` and returns `(e.code, dict(e.headers), e.read())`; lower-case header keys on both paths. `get_all` reads `x-pagination-page-count` from the first response and defaults to 1 when absent. In `wait_for_device_token`, sleep `interval` before each poll and stop with `AuthError` once `expires_in` seconds of sleeping have accumulated. Comment why the refresh body carries `redirect_uri` (Trakt's schema still requires it for the refresh grant).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_trakt -v`
Expected: 11 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add t2l/trakt.py tests/test_trakt.py
git commit -m "feat: trakt client with device auth, refresh, paging and 429 retry"
```

---

### Task 3: Commands, state and notification

**Files:**
- Create: `t2l/__main__.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `build_rows`, `write_parts` from Task 1; `TraktClient`, `AuthError`, `load_creds`, `save_creds` from Task 2.
- Produces:
  - `home() -> Path`: `$T2L_HOME` or `~/.config/trakt-to-letterboxd`, created with `parents=True, exist_ok=True`.
  - `load_state(path: Path) -> dict` with defaults `{"watched_at": None, "watermark": None, "exported_ids": [], "played": [], "pending": []}`; `save_state(path, state)`.
  - `notify(message: str) -> None`: runs `osascript -e 'display notification "<message>" with title "Trakt to Letterboxd"'` via `subprocess.run`; swallows failure.
  - `run(client, home_dir: Path, state: dict, notify=notify, tz=None) -> int`: the logic in the spec. Returns 0. Exact behaviour:
    1. `client.ensure_token()`.
    2. `acts = client.last_activities()["movies"]["watched_at"]`; if equal to `state["watched_at"]`, return 0 with no further calls.
    3. `start_at` is `state["watermark"]` minus 1 day as ISO `YYYY-MM-DDTHH:MM:SSZ`, or `None` on first run.
    4. `items = [i for i in client.history_movies(start_at) if i["type"] == "movie" and i["id"] not in exported]`.
    5. If items: `ratings = client.ratings_movies()`; `rows = build_rows(items, ratings, played, tz)`; extend `state["pending"]`, `state["exported_ids"]`; set `state["played"]`; `state["watermark"] = max watched_at`; `write_parts(state["pending"], home_dir)`; `notify(f"{len(rows)} new plays. Upload pending-*.csv at letterboxd.com/import then run mark-uploaded.")`.
    6. `state["watched_at"] = acts`; `save_state` happens in `main`, not here.
  - `mark_uploaded(home_dir: Path, state: dict) -> int`: moves `pending-*.csv` into `home_dir/"uploaded"/` renamed with a `YYYYMMDD-HHMMSS-` prefix, clears `state["pending"]`, returns 0.
  - `main(argv: list[str]) -> int`: subcommands `auth` (`--client-id`, `--client-secret` required), `run`, `mark-uploaded`. `auth` prints `Go to <verification_url> and enter <user_code>`, waits, saves creds, prints `Authorised.`. `run` loads creds and state, builds the client, calls `run`, saves creds if `client.creds_changed`, saves state. `AuthError` anywhere prints its message to stderr and returns 2. Entry: `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`.

- [ ] **Step 1: Write the failing tests**

`tests/test_main.py`:

```python
import json
import os
import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from unittest import mock

from t2l.__main__ import load_state, main, mark_uploaded, run, save_state
from t2l.trakt import AuthError


def item(hid, watched_at, trakt=1):
    return {"id": hid, "watched_at": watched_at, "action": "scrobble", "type": "movie",
            "movie": {"title": "The Matrix", "year": 1999,
                      "ids": {"trakt": trakt, "slug": "x", "imdb": "tt0133093", "tmdb": 603}}}


class FakeClient:
    def __init__(self, watched_at="2026-11-05T10:00:00.000Z", history=(), ratings=None, auth_error=None):
        self.watched_at, self.history, self.ratings = watched_at, list(history), ratings or {}
        self.auth_error, self.calls, self.creds_changed = auth_error, [], False

    def ensure_token(self):
        if self.auth_error:
            raise AuthError(self.auth_error)

    def last_activities(self):
        self.calls.append("last_activities")
        return {"movies": {"watched_at": self.watched_at}}

    def history_movies(self, start_at):
        self.calls.append(("history", start_at))
        return self.history

    def ratings_movies(self):
        self.calls.append("ratings")
        return self.ratings


class RunTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.notes = []

    def run_once(self, client, state=None):
        state = state or load_state(self.home / "state.json")
        code = run(client, self.home, state, notify=self.notes.append, tz=timezone.utc)
        return code, state

    def test_unchanged_activity_makes_no_history_call_or_notification(self):
        c = FakeClient()
        code, state = self.run_once(c, {"watched_at": "2026-11-05T10:00:00.000Z", "watermark": None,
                                        "exported_ids": [], "played": [], "pending": []})
        self.assertEqual((code, c.calls, self.notes), (0, ["last_activities"], []))

    def test_first_run_exports_full_history_and_notifies(self):
        c = FakeClient(history=[item(2, "2026-11-04T20:00:00Z"), item(1, "2026-11-01T20:00:00Z")], ratings={1: 9})
        code, state = self.run_once(c)
        self.assertEqual(c.calls, ["last_activities", ("history", None), "ratings"])
        self.assertEqual(state["watermark"], "2026-11-04T20:00:00Z")
        self.assertEqual(sorted(state["exported_ids"]), [1, 2])
        self.assertEqual(state["played"], [1])
        self.assertEqual([r["Rewatch"] for r in state["pending"]], ["", "true"])
        self.assertEqual(state["watched_at"], "2026-11-05T10:00:00.000Z")
        self.assertTrue((self.home / "pending-1.csv").exists())
        self.assertEqual(len(self.notes), 1)
        self.assertIn("2 new plays", self.notes[0])

    def test_incremental_run_overlaps_one_day_and_skips_exported(self):
        c = FakeClient(history=[item(2, "2026-11-04T20:00:00Z"), item(3, "2026-11-05T09:00:00Z")])
        state = {"watched_at": "old", "watermark": "2026-11-04T20:00:00Z", "exported_ids": [1, 2],
                 "played": [1], "pending": [{"tmdbID": "603", "imdbID": "tt0133093", "Title": "The Matrix",
                                             "Year": "1999", "WatchedDate": "2026-11-04", "Rewatch": "true", "Rating10": ""}]}
        self.run_once(c, state)
        self.assertEqual(c.calls[1], ("history", "2026-11-03T20:00:00Z"))
        self.assertEqual(len(state["pending"]), 2)
        self.assertEqual(sorted(state["exported_ids"]), [1, 2, 3])
        self.assertIn("1 new plays", self.notes[0])

    def test_new_activity_with_no_new_movies_is_quiet(self):
        c = FakeClient(history=[item(1, "2026-11-01T20:00:00Z")])
        state = {"watched_at": "old", "watermark": "2026-11-01T20:00:00Z", "exported_ids": [1], "played": [1], "pending": []}
        self.run_once(c, state)
        self.assertNotIn("ratings", c.calls)
        self.assertEqual(self.notes, [])
        self.assertEqual(state["watched_at"], "2026-11-05T10:00:00.000Z")


class MarkUploadedTest(unittest.TestCase):
    def test_moves_parts_and_clears_pending(self):
        home = Path(tempfile.mkdtemp())
        (home / "pending-1.csv").write_text("x")
        state = {"pending": [{"Title": "x"}]}
        self.assertEqual(mark_uploaded(home, state), 0)
        self.assertEqual(state["pending"], [])
        self.assertEqual(list(home.glob("pending-*.csv")), [])
        moved = list((home / "uploaded").glob("*-pending-1.csv"))
        self.assertEqual(len(moved), 1)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"T2L_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_run_without_creds_tells_user_to_auth(self):
        with mock.patch("sys.stderr") as err:
            self.assertEqual(main(["run"]), 2)
        self.assertIn("auth", "".join(str(c) for c in err.write.call_args_list))

    def test_auth_error_exits_2_and_keeps_state(self):
        (self.home / "creds.json").write_text(json.dumps({"client_id": "c", "client_secret": "s", "access_token": "a",
                                                          "refresh_token": "r", "expires_at": 0}))
        with mock.patch("t2l.__main__.TraktClient", return_value=FakeClient(auth_error="refresh failed (400): run auth again")):
            with mock.patch("sys.stderr"):
                self.assertEqual(main(["run"]), 2)
        self.assertFalse((self.home / "state.json").exists())

    def test_state_round_trip(self):
        path = self.home / "state.json"
        state = load_state(path)
        state["watermark"] = "w"
        save_state(path, state)
        self.assertEqual(load_state(path)["watermark"], "w")
        self.assertEqual(load_state(path)["pending"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_main -v`
Expected: FAIL with `ModuleNotFoundError: No module named 't2l.__main__'`

- [ ] **Step 3: Implement `t2l/__main__.py`**

Signatures and behaviour from the Interfaces block. `argparse` with subparsers. Compute the overlap start as `datetime.fromisoformat(watermark.replace("Z", "+00:00")) - timedelta(days=1)` formatted `%Y-%m-%dT%H:%M:%SZ`. `played` is stored as a sorted list in state and loaded into a `set` for `build_rows`. A missing `creds.json` raises `AuthError("no credentials: run `python3 -m t2l auth` first")`. Import `TraktClient` by name (`from t2l.trakt import TraktClient`) and write errors with `print(..., file=sys.stderr)` so the tests can patch both. One `shortcut:` comment on the overlap: plays that reach Trakt more than a day late are missed, widen the window if that bites.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all tests across the three files, `OK`

- [ ] **Step 5: Commit**

```bash
git add t2l/__main__.py tests/test_main.py
git commit -m "feat: auth, run and mark-uploaded commands with state and notification"
```

---

### Task 4: launchd schedule, README, first live run

**Files:**
- Create: `launchd/com.jaylane.trakt-to-letterboxd.plist`, `README.md`

**Interfaces:**
- Consumes: `python3 -m t2l run` from Task 3.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the plist**

`launchd/com.jaylane.trakt-to-letterboxd.plist` with placeholders `__PYTHON__` and `__REPO__`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jaylane.trakt-to-letterboxd</string>
  <key>ProgramArguments</key>
  <array><string>__PYTHON__</string><string>-m</string><string>t2l</string><string>run</string></array>
  <key>WorkingDirectory</key><string>__REPO__</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Minute</key><integer>0</integer></dict>
    <dict><key>Minute</key><integer>15</integer></dict>
    <dict><key>Minute</key><integer>30</integer></dict>
    <dict><key>Minute</key><integer>45</integer></dict>
  </array>
  <key>StandardOutPath</key><string>__HOME__/Library/Logs/trakt-to-letterboxd.log</string>
  <key>StandardErrorPath</key><string>__HOME__/Library/Logs/trakt-to-letterboxd.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Write `README.md`**

Sections, each a few lines: what it does and why the upload is manual (two sentences pointing at the spec); Trakt app setup (`https://trakt.tv/oauth/applications`, redirect URI `urn:ietf:wg:oauth:2.0:oob`, GitHub-linked account required); `python3 -m t2l auth --client-id ... --client-secret ...`; `python3 -m t2l run`; upload at `https://letterboxd.com/import/`, check the preview, then `python3 -m t2l mark-uploaded`; install the agent:

```bash
sed -e "s|__PYTHON__|$(command -v python3)|" -e "s|__REPO__|$PWD|" -e "s|__HOME__|$HOME|" \
  launchd/com.jaylane.trakt-to-letterboxd.plist > ~/Library/LaunchAgents/com.jaylane.trakt-to-letterboxd.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jaylane.trakt-to-letterboxd.plist
```

and uninstall with `launchctl bootout gui/$(id -u)/com.jaylane.trakt-to-letterboxd`. Add a "First run" warning: the first `run` exports the whole history and Letterboxd has no undo after confirming the preview; test one small file on a throwaway account first. Add "Limits" listing the four items under "Decisions the user may want to revisit" in the spec.

- [ ] **Step 3: Validate the plist renders and parses**

Run:
```bash
sed -e "s|__PYTHON__|$(command -v python3)|" -e "s|__REPO__|$PWD|" -e "s|__HOME__|$HOME|" launchd/com.jaylane.trakt-to-letterboxd.plist > /tmp/t2l.plist && plutil -lint /tmp/t2l.plist
```
Expected: `/tmp/t2l.plist: OK`

- [ ] **Step 4: Live smoke test against Trakt (needs the user's client id and secret)**

Run: `T2L_HOME=/tmp/t2l-smoke python3 -m t2l auth --client-id <id> --client-secret <secret>` then `T2L_HOME=/tmp/t2l-smoke python3 -m t2l run`
Expected: `Authorised.`, then a notification and `/tmp/t2l-smoke/pending-1.csv` whose first line is the header and whose row count matches the user's Trakt movie play count. Open the CSV and spot-check three rows against `https://trakt.tv/users/<me>/history/movies`. If credentials are not available, skip this step and say so in the task report; do not fabricate output.

- [ ] **Step 5: Commit**

```bash
git add launchd README.md
git commit -m "docs: launchd agent and setup instructions"
```
