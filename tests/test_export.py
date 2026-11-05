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
