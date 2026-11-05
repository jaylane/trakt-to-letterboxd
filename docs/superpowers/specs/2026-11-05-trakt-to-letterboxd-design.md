# Trakt to Letterboxd sync: design

Research: `docs/research/2026-11-05-trakt-letterboxd-brief.md` (32 claims
adversarially verified on 2026-11-05).

## What is possible

| Path | Status | Decision |
|---|---|---|
| Letterboxd API `POST /log-entries` | Exists, but access is "by request only" and Letterboxd states it is not granting access "for private or personal projects" | Not available. Email a request anyway; swap in later if a key arrives |
| Letterboxd inbound webhook, Zapier, IFTTT, email-to-log | None exist | Nothing to build on |
| Letterboxd CSV import at `https://letterboxd.com/import/` | Official, free, documented, dedups on film + WatchedDate | The write path |
| Automating the import page or internal JSON endpoints | Cloudflare challenge; terms §7 forbid circumventing security measures; the one tool that tried is archived | Rejected |
| Trakt outbound webhooks | None (request closed unimplemented 2026-04) | Poll `/sync/last_activities` instead |

So "as it occurs" is not achievable without breaking Letterboxd's terms. The
closest clean approximation: poll Trakt every 15 minutes, append new plays to a
pending CSV, and raise a macOS notification telling the user to upload it. The
upload itself is a drag-and-drop plus one confirmation click.

## Behaviour

A stdlib-only Python package `t2l` with three commands:

- `auth --client-id X --client-secret Y`: Trakt device-code flow. Prints the
  code and URL, polls until approved, saves credentials.
- `run`: the scheduled job. Refreshes the token if it expires within 5 minutes.
  Reads `/sync/last_activities`; exits silently if `movies.watched_at` matches
  the stored value. Otherwise fetches `/sync/history/movies` (all pages, 250 per
  page) from the stored watermark minus one day, or the full history on first
  run. Drops items already exported (by Trakt history `id`). Fetches
  `/sync/ratings/movies` for `Rating10`. Appends rows to the pending queue in
  state, rewrites `pending-N.csv` parts, and notifies if anything was added.
- `mark-uploaded`: run by the user after a successful import. Moves the CSV
  parts to `uploaded/` and clears the pending queue.

## CSV rules (from `https://letterboxd.com/about/importing-data/`)

- Columns, in this order: `tmdbID,imdbID,Title,Year,WatchedDate,Rewatch,Rating10`.
- UTF-8, comma delimiter, fields with commas or quotes are quoted, inner quotes
  escaped with a backslash, not doubled.
- `WatchedDate` is a calendar date `YYYY-MM-DD` in the machine's local zone
  (Trakt `watched_at` is UTC).
- `Rewatch` is `true` when an earlier play of the same Trakt movie exists,
  across runs; otherwise empty.
- `Rating10` is the movie's current Trakt rating (1-10) or empty.
- One row per play. Two plays on one calendar day collapse on Letterboxd's side.
- Files must stay under 1 MB; split at 1,000,000 bytes with the header repeated.

## State

Everything lives in `~/.config/trakt-to-letterboxd/` (override with `T2L_HOME`):

- `creds.json` (mode 0600): `client_id, client_secret, access_token,
  refresh_token, expires_at` (epoch seconds).
- `state.json`: `watched_at` (last seen activity stamp), `watermark` (max
  exported `watched_at`), `exported_ids` (Trakt history ids), `played` (Trakt
  movie ids with at least one exported play), `pending` (rows not yet uploaded).
- `pending-1.csv`, `pending-2.csv`, ... and `uploaded/`.

## Trakt facts the code relies on

- Auth host `https://auth.trakt.tv`, API host `https://api.trakt.tv`.
- Headers: `Content-Type: application/json`, `User-Agent: trakt-to-letterboxd/0.1`,
  `trakt-api-key`, `trakt-api-version: 2`, `Authorization: Bearer`.
- Device flow: `POST /oauth/device/code {client_id}`; poll
  `POST /oauth/device/token {code, client_id, client_secret}` every `interval`
  seconds. 200 done, 400 pending, 429 slow down, 410 expired, 418 denied.
- Refresh: `POST /oauth/token {refresh_token, client_id, client_secret,
  redirect_uri: "urn:ietf:wg:oauth:2.0:oob", grant_type: "refresh_token"}`.
  Refresh tokens are single use; 400 `invalid_grant` means re-run `auth`.
- Pagination headers `X-Pagination-Page`, `X-Pagination-Page-Count`; `limit`
  max 250.
- Rate limit 500 GET per 5 minutes; 429 carries `Retry-After`.
- History item: `{id, watched_at, action, type, movie: {title, year, ids: {trakt, slug, imdb, tmdb}}}`.
- Ratings item: `{rated_at, rating, movie: {ids: {trakt}}}`.

## Scheduling

launchd user agent with `StartCalendarInterval` at minutes 0, 15, 30, 45.
launchd runs a missed calendar job on wake; cron does not. Logs go to
`~/Library/Logs/trakt-to-letterboxd.log`.

## Out of scope

- Driving the Letterboxd website in any way.
- TV episodes (Letterboxd is films only).
- Exporting rating changes that have no new play.
- Watchlist, reviews, tags, likes.
- RSS-based verification of uploads.
- Webhook triggers from Plex, Jellyfin, or Tautulli (the poll is cheap enough).

## Decisions the user may want to revisit

1. Ratings are exported on every play row. A re-import can overwrite a rating
   set on Letterboxd with no undo. Set `Rating10` empty in `build_rows` to stop.
2. The first run exports the full history. Review the Letterboxd preview
   carefully; there is no undo after confirming.
3. Plays that reach Trakt more than a day after they happened are missed by the
   one-day overlap window.
4. Test one small CSV on a throwaway Letterboxd account first: the dedup and
   rating-overwrite behaviour rests on an undated help page.
