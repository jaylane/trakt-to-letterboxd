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
    def __init__(self, watched_at="2026-11-05T10:00:00.000Z", history=(), ratings=None, auth_error=None,
                 history_error=None, creds_changed=False):
        self.watched_at, self.history, self.ratings = watched_at, list(history), ratings or {}
        self.auth_error, self.history_error = auth_error, history_error
        self.calls, self.creds_changed = [], creds_changed
        self.creds = {"client_id": "c", "client_secret": "s", "access_token": "new", "refresh_token": "newr", "expires_at": 9}

    def ensure_token(self):
        if self.auth_error:
            raise AuthError(self.auth_error)

    def last_activities(self):
        self.calls.append("last_activities")
        return {"movies": {"watched_at": self.watched_at}}

    def history_movies(self, start_at):
        self.calls.append(("history", start_at))
        if self.history_error:
            raise self.history_error
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
                 "played": [1], "pending": []}
        self.run_once(c, state)
        self.assertEqual(c.calls[1], ("history", "2026-11-03T20:00:00Z"))
        self.assertEqual(len(state["pending"]), 1)
        self.assertEqual(sorted(state["exported_ids"]), [1, 2, 3])
        self.assertIn("1 new plays", self.notes[0])

    def test_outstanding_upload_defers_new_plays(self):
        c = FakeClient(history=[item(3, "2026-11-05T09:00:00Z")])
        pending = [{"tmdbID": "603", "imdbID": "tt0133093", "Title": "The Matrix", "Year": "1999",
                    "WatchedDate": "2026-11-04", "Rewatch": "", "Rating10": ""}]
        state = {"watched_at": "old", "watermark": "2026-11-04T20:00:00Z", "exported_ids": [2],
                 "played": [1], "pending": list(pending)}
        code, state = self.run_once(c, state)
        self.assertEqual((code, c.calls, self.notes), (0, [], []))
        self.assertEqual(state["pending"], pending)
        self.assertEqual(state["watched_at"], "old")

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

    def test_auth_error_is_notified(self):
        (self.home / "creds.json").write_text(json.dumps({"client_id": "c", "client_secret": "s"}))
        with mock.patch("t2l.__main__.TraktClient", return_value=FakeClient(auth_error="refresh failed (400): run auth again")):
            with mock.patch("t2l.__main__.notify") as note, mock.patch("sys.stderr"):
                self.assertEqual(main(["run"]), 2)
        self.assertIn("refresh failed", note.call_args[0][0])

    def test_refreshed_token_is_saved_when_a_later_call_fails(self):
        (self.home / "creds.json").write_text(json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "old"}))
        fake = FakeClient(history_error=RuntimeError("GET /sync/history/movies failed (502)"), creds_changed=True)
        with mock.patch("t2l.__main__.TraktClient", return_value=fake):
            with mock.patch("t2l.__main__.notify") as note, mock.patch("sys.stderr") as err:
                self.assertEqual(main(["run"]), 1)
        self.assertEqual(json.loads((self.home / "creds.json").read_text())["refresh_token"], "newr")
        self.assertFalse((self.home / "state.json").exists())
        self.assertIn("502", note.call_args[0][0])
        self.assertIn("502", "".join(str(c) for c in err.write.call_args_list))

    def test_state_round_trip(self):
        path = self.home / "state.json"
        state = load_state(path)
        state["watermark"] = "w"
        save_state(path, state)
        self.assertEqual(load_state(path)["watermark"], "w")
        self.assertEqual(load_state(path)["pending"], [])


if __name__ == "__main__":
    unittest.main()
