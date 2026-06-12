"""
Fetch World Cup match results from The Odds API /scores endpoint.

  Cost: 1 credit per call regardless of how many days we pull.
  Strategy: ask for the last 3 days; merge into data/results_history.json.
"""

from __future__ import annotations
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fetch_odds import API_KEY, SPORT_KEY, BASE_URL, _http_get

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_PATH = DATA_DIR / "results_history.json"


def fetch_scores(days_from: int = 3) -> dict:
    """Call /scores and return parsed payload + remaining credits.

    The Odds API returns HTTP 422 when the sport has no completed events in
    the requested window (typical case before the tournament starts). We
    treat that as "empty results" rather than an error so the pipeline keeps
    running and the page can still regenerate from odds alone.
    """
    import urllib.error
    params = {
        "apiKey": API_KEY,
        "daysFrom": str(days_from),
        "dateFormat": "iso",
    }
    url = f"{BASE_URL}/sports/{SPORT_KEY}/scores/?{urllib.parse.urlencode(params)}"
    try:
        body, headers = _http_get(url)
    except urllib.error.HTTPError as e:
        if e.code == 422:
            return {
                "events": [],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "remaining_credits": e.headers.get("x-requests-remaining"),
                "used_credits": e.headers.get("x-requests-used"),
                "note": "no completed events yet (422 from /scores)",
            }
        raise
    return {
        "events": body,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "remaining_credits": headers.get("remaining"),
        "used_credits": headers.get("used"),
    }


def load_results_history() -> dict:
    if not RESULTS_PATH.exists():
        return {}
    with open(RESULTS_PATH, "r") as f:
        return json.load(f)


def save_results_history(history: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def update_results(days_from: int = 3, only_if_needed: bool = False) -> dict:
    """
    Fetch latest scores and merge into results_history.json.

    only_if_needed=True: skip the API call entirely if every match that's
    past its kickoff is already marked completed in the local results
    history. Saves 2 credits per cron run on rest days / when nothing
    new has finished.

    Each merged event has:
      {event_id: {
          home_team, away_team, commence_time, completed,
          home_score, away_score, last_update
      }}
    """
    if only_if_needed:
        import json as _json
        history = load_results_history()
        events_path = DATA_DIR / "events.json"
        # Quick check: is there at least one event whose kickoff has passed
        # and that we don't yet have a completed record for?
        needs_fetch = False
        try:
            with open(events_path) as f:
                events = _json.load(f)
            now = datetime.now(timezone.utc).isoformat()
            for e in events:
                ev_id = e.get("id")
                kickoff = e.get("commence_time", "")
                if kickoff and kickoff < now:
                    rec = history.get(ev_id)
                    if not rec or not rec.get("completed"):
                        needs_fetch = True
                        break
        except Exception:
            needs_fetch = True  # if anything goes wrong, fetch to be safe

        if not needs_fetch:
            return {
                "n_total": len(history), "n_new": 0, "n_updated": 0,
                "remaining_credits": None, "used_credits": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "skipped": "no new completed matches expected",
            }

    payload = fetch_scores(days_from=days_from)
    history = load_results_history()
    n_new, n_updated = 0, 0

    for ev in payload["events"]:
        ev_id = ev.get("id")
        if not ev_id:
            continue
        scores = ev.get("scores") or []
        home_score, away_score = None, None
        for s in scores:
            try:
                val = int(s["score"])
            except (KeyError, ValueError, TypeError):
                continue
            if s.get("name") == ev.get("home_team"):
                home_score = val
            elif s.get("name") == ev.get("away_team"):
                away_score = val
        record = {
            "home_team": ev.get("home_team"),
            "away_team": ev.get("away_team"),
            "commence_time": ev.get("commence_time"),
            "completed": bool(ev.get("completed")),
            "home_score": home_score,
            "away_score": away_score,
            "last_update": payload["fetched_at"],
        }
        if ev_id not in history:
            history[ev_id] = record
            n_new += 1
        elif history[ev_id] != record:
            history[ev_id] = record
            n_updated += 1

    save_results_history(history)
    return {
        "n_total": len(history),
        "n_new": n_new,
        "n_updated": n_updated,
        "remaining_credits": payload["remaining_credits"],
        "used_credits": payload["used_credits"],
        "fetched_at": payload["fetched_at"],
    }


if __name__ == "__main__":
    summary = update_results()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
