"""Commands: auth, run, mark-uploaded."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from t2l.export import build_rows, write_parts
from t2l.trakt import AuthError, TraktClient, load_creds, save_creds

TITLE = "Trakt to Letterboxd"


def home():
    path = Path(os.environ.get("T2L_HOME") or Path.home() / ".config" / "trakt-to-letterboxd")
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_state(path):
    state = {"watched_at": None, "watermark": None, "exported_ids": [], "played": [], "pending": []}
    if Path(path).exists():
        state.update(json.loads(Path(path).read_text()))
    return state


def save_state(path, state):
    Path(path).write_text(json.dumps(state, indent=1))


def notify(message):
    script = f'display notification "{message}" with title "{TITLE}"'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def _overlap_start(watermark):
    # shortcut: plays that reach Trakt more than a day after they happened are missed; widen if that bites.
    stamp = datetime.fromisoformat(watermark.replace("Z", "+00:00")) - timedelta(days=1)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(client, home_dir, state, notify=notify, tz=None):
    # An outstanding upload must not change the files the user is about to import;
    # anything new waits for mark-uploaded and is picked up from the watermark.
    if state["pending"]:
        return 0
    client.ensure_token()
    acts = client.last_activities()["movies"]["watched_at"]
    if acts == state["watched_at"]:
        return 0
    exported = set(state["exported_ids"])
    start_at = _overlap_start(state["watermark"]) if state["watermark"] else None
    items = [i for i in client.history_movies(start_at) if i["type"] == "movie" and i["id"] not in exported]
    if items:
        played = set(state["played"])
        rows = build_rows(items, client.ratings_movies(), played, tz)
        state["pending"].extend(rows)
        state["exported_ids"] = sorted(exported | {i["id"] for i in items})
        state["played"] = sorted(played)
        state["watermark"] = max([state["watermark"] or ""] + [i["watched_at"] for i in items])
        write_parts(state["pending"], home_dir)
        notify(f"{len(rows)} new plays. Upload pending-*.csv at letterboxd.com/import then run mark-uploaded.")
    state["watched_at"] = acts
    return 0


def mark_uploaded(home_dir, state):
    done = Path(home_dir) / "uploaded"
    done.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for part in sorted(Path(home_dir).glob("pending-*.csv")):
        part.rename(done / f"{stamp}-{part.name}")
    state["pending"] = []
    return 0


def _client(home_dir):
    path = home_dir / "creds.json"
    if not path.exists():
        raise AuthError("no credentials: run `python3 -m t2l auth` first")
    return TraktClient(load_creds(path))


def main(argv):
    parser = argparse.ArgumentParser(prog="python3 -m t2l")
    sub = parser.add_subparsers(dest="cmd", required=True)
    auth = sub.add_parser("auth")
    auth.add_argument("--client-id", required=True)
    auth.add_argument("--client-secret", required=True)
    sub.add_parser("run")
    sub.add_parser("mark-uploaded")
    args = parser.parse_args(argv)
    home_dir = home()
    state_path = home_dir / "state.json"
    try:
        if args.cmd == "auth":
            client = TraktClient({"client_id": args.client_id, "client_secret": args.client_secret})
            device = client.device_code()
            print(f"Go to {device['verification_url']} and enter {device['user_code']}")
            client.wait_for_device_token(device)
            save_creds(home_dir / "creds.json", client.creds)
            print("Authorised.")
            return 0
        state = load_state(state_path)
        if args.cmd == "mark-uploaded":
            code = mark_uploaded(home_dir, state)
        else:
            client = _client(home_dir)
            try:
                code = run(client, home_dir, state)
            finally:
                # Trakt refresh tokens are single use: persist a rotation even when a later call fails.
                if client.creds_changed:
                    save_creds(home_dir / "creds.json", client.creds)
        save_state(state_path, state)
        return code
    except AuthError as e:
        print(str(e), file=sys.stderr)
        notify(str(e))
        return 2
    except Exception as e:  # launchd shows nothing; the notification is the only signal the user gets
        message = f"{type(e).__name__}: {e}"
        print(message, file=sys.stderr)
        notify(message)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
