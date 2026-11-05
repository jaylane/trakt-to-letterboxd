# Trakt -> Letterboxd sync: decision brief (2026-11-05)

**Verdict:** no sanctioned real-time path exists. Letterboxd's API is request-only and refuses personal projects; Trakt has no outbound webhooks. The robust, terms-clean design is a scheduled script that polls Trakt, writes an incremental Letterboxd CSV, and leaves the upload as a short manual browser step.

## 1. Letterboxd write surfaces

| Surface | Status | Verdict |
|---|---|---|
| Official API `https://api.letterboxd.com/api/v0` | Has member writes (`POST /log-entries`, `PATCH`/`DELETE /log-entry/{id}`, scopes `user` + `content:modify`), but "Access to the Letterboxd API is available by request only" and "we are not granting access ... for LLM or GPT-related use, for private or personal projects" (https://letterboxd.com/api-beta/, read via https://web.archive.org/web/20261105171014/https://letterboxd.com/api-beta/). Correction: the API does have write endpoints; access, not capability, is the block. | Unavailable |
| CSV importer `https://letterboxd.com/import/` | Official; "Importing is free for all members" (https://letterboxd.com/faq/); not a Pro/Patron feature (https://letterboxd.com/about/pro/). Logged-in browser only; returns a Cloudflare managed challenge (403, `cf-mitigated: challenge`) to scripts. | Use this |
| Website internal JSON `POST https://letterboxd.com/api/v0/production-log-entries` | Verified in the live bundle (https://s.ltrbxd.com/static/js/es/chunks/CrcF8Bna.js): body `{productionId, diaryDetails, rating, like, tags, review}`, `X-CSRF-TOKEN` from `window.supermodelCSRF`, session cookies. Needs a browser-minted `cf_clearance` pinned to IP/UA. Correction: `/s/save-diary-entry` still ships as the no-JS form action; its reported 404 is unverified. The importer's own POSTs are documented too (correction to "undocumented"): `POST /import/watchlist/match-import-film/` then `POST /s/save-users-imported-imdb-history` (https://github.com/hlamhuy/plexterbox/blob/main/letterboxd/import.go). | Reject: fragile, undocumented |
| Webhooks, email-to-log, Trakt/Plex integration | None. | N/A |
| RSS `https://letterboxd.com/{user}/rss/` | Read-only, ~50 entries, carries `tmdb:movieId`, `letterboxd:watchedDate`, `letterboxd:rewatch`. | Verification only |

**Terms** (https://letterboxd.com/legal/terms-of-use/, "Last updated: October 2026"). §6.11: "Except as explicitly authorized in these Terms, you must not employ any robot, spider, scraper, deep-link, or other automated data gathering or extraction tool, program, or algorithm to access, acquire, copy, or monitor any portion of the Service." §7: "You agree to access the Service through the interface we provide" and no circumventing "security or authentication measures". §21: termination "for any reason or no reason whatsoever". Correction: §6.11 targets gathering/extraction, so a headless browser submitting the import form is an interpretation question; cookie replay and Cloudflare bypass fall plainly under §7.

## 2. Trakt read surface

- **Docs.** Apiary is deprecated; use https://developer.trakt.tv and https://github.com/trakt/trakt-api (https://github.com/trakt/trakt-api/discussions/808).
- **App creation** needs a verified GitHub account, not VIP, and the policy permits "A personal script that migrates a user's own watch history ... to their account on another platform" (https://developer.trakt.tv/docs/api-use-policy, 2026-10-20). Correction: the mid-2026 VIP-only gate (https://github.com/trakt/trakt-api/issues/902) is superseded. Unused apps are deleted after 30 days; free accounts connect at most two Community Apps and yours counts (https://github.com/trakt/trakt-api/discussions/943).
- **Headers.** `Content-Type: application/json`, `User-Agent: name/version` (required, https://github.com/trakt/trakt-api/discussions/655), `trakt-api-key`, `trakt-api-version: 2`, `Authorization: Bearer`.
- **Auth.** Every OAuth call goes to `https://auth.trakt.tv` (https://github.com/trakt/trakt-api/blob/master/projects/api/src/contracts/oauth/index.ts). Device flow: `POST /oauth/device/code {client_id}`, poll `POST /oauth/device/token {code, client_id}` (`client_secret` optional, deprecated) every `interval` seconds; 400 pending, 429 slow down, 410 expired. Refresh: `POST /oauth/token` with `grant_type=refresh_token` plus `redirect_uri` (still required in the schema; use `urn:ietf:wg:oauth:2.0:oob`). Refresh tokens are single-use; failure is 400 `invalid_grant` "session not found", meaning re-auth. Correction on lifetime: the guide says 7 days, samples say `expires_in: 86400`, Trakt's March 2025 notice said 24 hours; read `expires_in`. Refresh-token lifetime is undocumented.
- **Endpoints.** `GET /sync/history/movies?start_at&end_at&page&limit` returns `{id, watched_at (UTC ISO), action: scrobble|checkin|watch, movie{title, year, ids{trakt, slug, imdb, tmdb}}}`, newest first, one item per play. `GET /sync/ratings/movies` returns `{rated_at, rating 1-10, movie}`. `GET /sync/last_activities` returns `movies.watched_at`, `movies.rated_at`: "Cache these dates locally and compare them before syncing" (https://github.com/trakt/trakt-api/blob/master/projects/api/src/contracts/sync/index.ts). Avoid `/users/{u}/watched/movies`: it only carries `last_watched_at`, losing rewatches (https://github.com/bbeesley/trakt-to-letterboxd/issues/765).
- **Pagination.** `X-Pagination-Page/Limit/Page-Count/Item-Count`; `limit` clamped at 250, history default 100 (https://github.com/trakt/trakt-api/pull/925). Read headers, not your request.
- **Rate limits** (https://developer.trakt.tv/docs/rate-limiting, 2026-10-13): 500 authed GET per 5 min, 1 POST/s; 429 usually has `Retry-After` and `X-Ratelimit`, abuse-layer 429s may omit both.
- **Webhooks.** None outbound; the only webhook in the contract is the inbound VIP Plex scrobbler URL (https://github.com/trakt/trakt-api/blob/master/projects/api/src/contracts/users/schema/response/plexSettingsResponseSchema.ts). Request #10 closed unimplemented 2026-04-07 (https://github.com/trakt/trakt-api/issues/10). IFTTT polls hourly free, 15 min Starter, 5 min Pro (https://ifttt.com/trakt); neither service has a Zapier app.

## 3. CSV import format

Source: https://letterboxd.com/about/importing-data/ (page carries no revision date).

- **File.** UTF-8; comma delimiter, no space after; quote fields with commas; escape inner quotes with a backslash, not doubling. 1 MB per file, header row in every part. No row limit stated.
- **Header.** At least one of `LetterboxdURI`, `tmdbID`, `imdbID`, `Title`; any order; unknown columns ignored.
- **Matching.** `LetterboxdURI`, `tmdbID` (numeric), `imdbID` (`tt...`) are exact; `Title` + `Year` + `Directors` is best-guess. tmdbID vs imdbID precedence undocumented.
- **Columns.** `Rating` 0.5-5 in 0.5 steps; `Rating10` integer 1-10, converted; with both, "whichever column appears second in the file will take precedence". `WatchedDate` `YYYY-MM-DD` creates a diary entry; "calendar dates ... not timestamps", convert UTC to local first. `Rewatch` (`true`), `Tags`, `Review` attach only when `WatchedDate` is present; an undated `Review` becomes an undated review.
- **No date** marks watched only. All profile imports mark watched; there is no mode switch.
- **Dedup.** "The importer updates existing Diary Entries if a film is imported with a WatchedDate that matches an existing entry for the same film"; same film + same date rows in one file "will be combined into a single entry". Different date = new entry. Two plays on one calendar day collapse. A third-party README warns re-import overwrites ratings with no undo (https://github.com/stevebargelt/trakt-letterboxd).
- **Safety.** Preview screen before commit, where TV rows mismatched to similarly named films must be removed by hand; "There is no undo after the confirmation step." Filter `type == movie` at the source.

## 4. Existing tools

| Tool | Approach | Maintained? | Borrow |
|---|---|---|---|
| carsoreo/letterboxd-trakt-sync (https://github.com/carsoreo/letterboxd-trakt-sync) | Python stdlib, device auth, `state.json` watermark, CSV for manual upload, RSS loop prevention | Yes, 2026-10-21 | State/watermark design; its stance "Letterboxd's terms don't allow bots on the website" |
| anoopsankar/Trakt2Letterboxd (https://github.com/anoopsankar/Trakt2Letterboxd) | Device auth, `/sync/history/movies`, row per play, full export, no ratings or Rewatch | Yes, 2026-04-02 | Listed on Letterboxd's import page; note it emits raw `watched_at`, a bug to avoid |
| JohanDevl/Export_Trakt_4_Letterboxd (https://github.com/JohanDevl/Export_Trakt_4_Letterboxd) | Go/Docker, built-in cron, `history_mode = individual`, auth-code with localhost callback, CSV only | Dependabot only | Per-play vs aggregated switch |
| bbeesley/trakt-to-letterboxd (https://github.com/bbeesley/trakt-to-letterboxd) | Unauthenticated public endpoints, hard-coded client id | Broken since 2026-02 (issue #764) | Nothing |
| louiscrc/trakt-to-letterboxd (https://github.com/louiscrc/trakt-to-letterboxd) | Selenium upload; plaintext password; headless "systematically re-triggers Cloudflare" | Archived 2026-09-17 | Diff key `imdbID+WatchedDate+Rating10`; rewatch = prior play exists |
| JvSomeren/tautulli-watched-sync, buraglio/trakt2lboxd (https://github.com/buraglio/trakt2lboxd) | Official `/log-entries` with a granted key. Correction: two such tools, not one | 2020; one day in 2026-04 | `LogEntryCreationRequest` shape, 409 on duplicate, if a key ever arrives |
| willtho89/librarySync, Vandaahl/PlexBridge, Jellyscribe | App-extracted client secret or copied cookies plus curl-impersonate to internal endpoints | Active, self-described "fragile" | Nothing |

## 5. Real-time options

| Option | Robustness | Terms | Verdict |
|---|---|---|---|
| Poll `/sync/last_activities` every 5-15 min, then write CSV | Solid; ~150-300 GET/day against 500 per 5 min | Trakt permits; Letterboxd upload remains manual | Near-real-time export |
| Headless browser on `/import` | Turnstile blocks headless; `cf_clearance` is IP/UA-pinned and short-lived; only tool archived | §6.11 arguable, §7 arguable, §21 any-reason termination | Reject |
| Cookie replay to `production-log-entries` or importer POSTs | Same Cloudflare wall; endpoints already changed once | Circumvents security measures (§7) | Reject |
| Mobile-app-extracted API credentials | Works until revoked; Jellyscribe: "not an integration Letterboxd has approved" | Credential misuse | Reject |
| Apply for API access | "unable to individually reply, or to guarantee access"; personal projects excluded | Clean | Email anyway; do not plan on it |
| Media-server webhook as trigger: Plex webhooks need Plex Pass and document only lowercase `Metadata.guid` (https://support.plex.tv/articles/115002267687-webhooks/); Tautulli Watched trigger at 85% with `{themoviedb_id}` needs no Plex Pass; Jellyfin Webhook plugin `PlaybackStop` + `PlayedToCompletion` (https://github.com/jellyfin/jellyfin-plugin-webhook) | Good event capture | Clean | Trigger for the CSV generator only |

Trakt's own Plex Scrobbler needs Trakt VIP plus Plex Pass (https://web.archive.org/web/20240718211449/https://support.trakt.tv/support/solutions/articles/70000425640-plex-scrobbler; the live URL now 404s).

## 6. Recommended architecture

- **One stdlib Python script scheduled by launchd** with `StartCalendarInterval`, which runs missed jobs on wake; cron and `StartInterval` skip them (https://keith.github.io/xcode-man-pages/launchd.plist.5.html). No dependencies, following carsoreo.
- **Device-code OAuth on `auth.trakt.tv`**, token pair persisted and replaced after every refresh; on `invalid_grant`, re-run device auth and notify. GitHub-linked app, real `User-Agent`.
- **Change detection via `/sync/last_activities`**: store `movies.watched_at` and `movies.rated_at`, exit early when unchanged; otherwise pull `/sync/history/movies?start_at=<watermark minus 1 day>&limit=250`, paging by `X-Pagination-Page-Count`. The overlap is harmless because the importer merges same film + same date.
- **Normalize to Letterboxd rules**: keep `type == movie`; convert `watched_at` to the chosen zone before truncating; emit `tmdbID,imdbID,Title,Year,WatchedDate,Rewatch,Rating10`; `Rewatch=true` when an earlier play exists for the same ids; latest rating by `movie.ids.trakt`.
- **Dedup on Trakt `history.id`** in the state file; the Letterboxd side dedups on film+date. Split output at 1 MB with the header repeated.
- **Upload stays human**: raise a macOS notification when `pending.csv` is non-empty; the user drops it on https://letterboxd.com/import/, checks the preview, confirms, then marks it uploaded (a flag or deleting the file) so the script archives it.
- **Verify via RSS** after upload: match `tmdb:movieId` + `watchedDate` for the last 50 entries; this also stops re-export of entries already present.
- **Email the API application** naming the project; if a key arrives, swap the CSV writer for `POST /log-entries` using buraglio's client as reference. Build nothing that drives the browser, replays cookies, or extracts app credentials: each is a maintenance treadmill with account loss as the downside.

**Open questions for the user**

1. Is a manual upload a few times a week acceptable, or does "as it occurs" justify terms and termination risk? The design above assumes the former.
2. Timezone for `WatchedDate`: fixed home zone, or the machine's zone at run time?
3. Rewatches: one row per play or one per film? Same-day double plays collapse either way.
4. Export ratings at all, given re-import may overwrite an existing Letterboxd rating with no undo?
5. Backfill the full history once (several 1 MB files, careful preview) or start the clock at first run?
6. Which player feeds Trakt (Plex, Jellyfin, Kodi, mobile check-in)? Tautulli or Jellyfin webhooks could trigger the generator instantly instead of waiting on the poll.
7. Test on a throwaway account first: same-film-same-date update, `Rewatch` literal case, and rating overwrite on re-import. The help page has no revision date.

Skipped: no live calls to either API; every fact comes from the supplied research and verification bundle. Risk: Letterboxd's dedup and rating-overwrite behaviour rest on an undated help page and need a test import before trusting idempotent re-runs.