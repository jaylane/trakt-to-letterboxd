"""Trakt history items to Letterboxd import CSV."""
from datetime import datetime
from pathlib import Path

COLUMNS = ["tmdbID", "imdbID", "Title", "Year", "WatchedDate", "Rewatch", "Rating10"]


def watched_date(watched_at, tz=None):
    # Letterboxd wants the local calendar date; Trakt gives a UTC instant.
    stamp = datetime.fromisoformat(watched_at.replace("Z", "+00:00"))
    return stamp.astimezone(tz).date().isoformat()


def _s(value):
    return "" if value is None else str(value)


def build_rows(items, ratings, played, tz=None):
    rows = []
    for item in sorted(items, key=lambda i: i["watched_at"]):
        movie = item["movie"]
        ids = movie.get("ids", {})
        trakt_id = ids.get("trakt")
        rows.append({
            "tmdbID": _s(ids.get("tmdb")),
            "imdbID": _s(ids.get("imdb")),
            "Title": _s(movie.get("title")),
            "Year": _s(movie.get("year")),
            "WatchedDate": watched_date(item["watched_at"], tz),
            "Rewatch": "true" if trakt_id in played else "",
            "Rating10": _s(ratings.get(trakt_id)),
        })
        played.add(trakt_id)
    return rows


def _field(value):
    # Letterboxd's importer escapes inner quotes with a backslash, not by doubling.
    if any(c in value for c in ',"\n\r'):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _render(rows):
    header = ",".join(COLUMNS) + "\n"
    lines = [",".join(_field(row[c]) for c in COLUMNS) + "\n" for row in rows]
    return header, lines


def write_parts(rows, out_dir, limit=1_000_000):
    out_dir = Path(out_dir)
    for stale in out_dir.glob("pending-*.csv"):
        stale.unlink()
    if not rows:
        return []
    header, lines = _render(rows)
    parts, part, size = [], [], len(header.encode("utf-8"))
    for line in lines:
        n = len(line.encode("utf-8"))
        if part and size + n >= limit:
            parts.append(part)
            part, size = [], len(header.encode("utf-8"))
        part.append(line)
        size += n
    parts.append(part)
    paths = []
    for i, part in enumerate(parts, 1):
        path = out_dir / f"pending-{i}.csv"
        path.write_text(header + "".join(part), encoding="utf-8")
        paths.append(path)
    return paths
