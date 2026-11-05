# trakt-to-letterboxd

Polls your Trakt.tv movie history every 15 minutes and writes new plays into a
Letterboxd import CSV, then notifies you to upload it. The upload stays manual:
Letterboxd's API is not granted to personal projects and automating the import
page breaks its terms of use. See `docs/superpowers/specs/` for the research.

Standard library only. Python 3.9 or newer. Tests: `python3 -m unittest`.

## Setup

1. Create a Trakt API app at https://trakt.tv/oauth/applications (needs a
   GitHub-linked Trakt account). Redirect URI: `urn:ietf:wg:oauth:2.0:oob`.
2. Authorise once:

   ```bash
   python3 -m t2l auth --client-id <id> --client-secret <secret>
   ```

   Enter the printed code at the printed URL. Credentials land in
   `~/.config/trakt-to-letterboxd/creds.json` (mode 0600). Set `T2L_HOME` to
   use a different directory.

## First run

The first `run` exports your whole Trakt movie history. Letterboxd has no undo
after you confirm the import preview, so try one small file on a throwaway
account first.

```bash
python3 -m t2l run
```

## Each time you get a notification

1. Go to https://letterboxd.com/import/ and drop
   `~/.config/trakt-to-letterboxd/pending-*.csv` on it (several files if your
   history is over 1 MB).
2. Check the preview, remove any wrong matches, confirm.
3. Tell the tool so it stops re-exporting those rows:

   ```bash
   python3 -m t2l mark-uploaded
   ```

While a CSV is pending, scheduled runs leave it untouched and new plays wait
for `mark-uploaded`; they are picked up on the next run.

## Schedule it

launchd runs a missed calendar job on wake; cron does not. Keep the repo
outside `~/Documents`, `~/Desktop` and `~/Downloads`: macOS blocks background
agents from those folders unless you grant the Python binary Full Disk Access.

```bash
sed -e "s|__PYTHON__|$(command -v python3)|" -e "s|__REPO__|$PWD|" -e "s|__HOME__|$HOME|" \
  launchd/com.jaylane.trakt-to-letterboxd.plist > ~/Library/LaunchAgents/com.jaylane.trakt-to-letterboxd.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jaylane.trakt-to-letterboxd.plist
```

Check the first scheduled run with:

```bash
launchctl print gui/$(id -u)/com.jaylane.trakt-to-letterboxd | grep "last exit"
```

Logs: `~/Library/Logs/trakt-to-letterboxd.log`. Errors also raise a
notification. Uninstall:

```bash
launchctl bootout gui/$(id -u)/com.jaylane.trakt-to-letterboxd
```

## Limits

- Ratings go on every play row. A re-import can overwrite a rating you set on
  Letterboxd, with no undo. Blank `Rating10` in `t2l/export.py` to stop that.
- Plays that reach Trakt more than a day after they happened are missed.
- Two plays of one film on the same calendar day collapse into one Letterboxd
  entry.
- Letterboxd's dedup-on-reimport behaviour comes from an undated help page.
