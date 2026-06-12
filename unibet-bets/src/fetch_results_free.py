"""
Free, no-auth source for WC 2026 match results: ESPN public scoreboard.

Replaces the /scores call from The Odds API (2 credits each) with a zero-
cost HTTPS GET to ESPN's public scoreboard endpoint:

  http://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard

ESPN uses slightly different team names than The Odds API, so we maintain
a mapping (ESPN → Odds-API canonical) and match by event_id from local
events.json when possible, falling back to team-pair lookup.

Same output shape as fetch_results.update_results so the rest of the
pipeline doesn't care which source supplied the data.
"""

from __future__ import annotations
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_PATH = DATA_DIR / "results_history.json"
EVENTS_PATH = DATA_DIR / "events.json"

ESPN_BASE = "http://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"

# ESPN display name → Odds API canonical name. Only override when they differ.
ESPN_TO_ODDS = {
    "Czechia": "Czech Republic",
    "United States": "USA",
    "Bosnia-Herzegovina": "Bosnia & Herzegovina",
    "Korea Republic": "South Korea",
    "Ivory Coast": "Ivory Coast",  # same
    "Cape Verde Islands": "Cape Verde",
    "Cape Verde": "Cape Verde",
    "DR Congo": "DR Congo",
    "Curacao": "Curaçao",
    "Curaçao": "Curaçao",
}


def _normalize(name: str) -> str:
    return ESPN_TO_ODDS.get(name, name)


def _fetch_espn(date_str: str | None = None) -> dict:
    """date_str format: YYYYMMDD or YYYYMMDD-YYYYMMDD; None = today/upcoming."""
    url = ESPN_BASE
    if date_str:
        url = f"{ESPN_BASE}?dates={date_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_results_history() -> dict:
    if not RESULTS_PATH.exists():
        return {}
    with open(RESULTS_PATH) as f:
        return json.load(f)


def save_results_history(history: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def load_events_index() -> dict:
    """Returns {(home_norm, away_norm): event_id} from local events.json."""
    if not EVENTS_PATH.exists():
        return {}
    with open(EVENTS_PATH) as f:
        events = json.load(f)
    return {(e["home_team"], e["away_team"]): e["id"] for e in events}


def update_results_free(days_back: int = 4) -> dict:
    """
    Pull last `days_back` days of WC scoreboard from ESPN, merge completed
    matches into results_history.json. Zero API credits used.
    """
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days_back)
    date_str = f"{start.strftime('%Y%m%d')}-{today.strftime('%Y%m%d')}"

    try:
        data = _fetch_espn(date_str)
    except Exception as e:
        return {"error": f"ESPN fetch failed: {e}", "n_new": 0, "n_updated": 0}

    history = load_results_history()
    events_index = load_events_index()
    n_new, n_updated = 0, 0

    for ev in data.get("events", []):
        status = ev.get("status", {}).get("type", {})
        completed = bool(status.get("completed"))
        if not completed:
            # We only care about finished matches for results tracking
            continue

        comps = ev.get("competitions", [])
        if not comps:
            continue
        competitors = comps[0].get("competitors", [])
        if len(competitors) < 2:
            continue

        # ESPN gives team-with-score pairs; figure out which is home/away
        home_name, away_name = None, None
        home_score, away_score = None, None
        for c in competitors:
            team = c.get("team", {}).get("displayName", "")
            team_canon = _normalize(team)
            score = c.get("score")
            try:
                score_int = int(score) if score is not None else None
            except (ValueError, TypeError):
                score_int = None
            if c.get("homeAway") == "home":
                home_name = team_canon; home_score = score_int
            elif c.get("homeAway") == "away":
                away_name = team_canon; away_score = score_int

        if not home_name or not away_name:
            continue

        # Match to a local event_id by team pair (Odds API uses the same key)
        event_id = events_index.get((home_name, away_name))
        if not event_id:
            # Try reversed (ESPN home/away convention can differ)
            event_id = events_index.get((away_name, home_name))
            if event_id:
                home_name, away_name = away_name, home_name
                home_score, away_score = away_score, home_score

        if not event_id:
            # Event not in our local Odds-API events list; skip silently
            continue

        record = {
            "home_team": home_name,
            "away_team": away_name,
            "commence_time": ev.get("date"),
            "completed": True,
            "home_score": home_score,
            "away_score": away_score,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
        if event_id not in history:
            history[event_id] = record
            n_new += 1
        elif history[event_id] != record:
            history[event_id] = record
            n_updated += 1

    save_results_history(history)
    return {
        "source": "ESPN public scoreboard",
        "n_total": len(history),
        "n_new": n_new,
        "n_updated": n_updated,
        "credits_spent": 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    summary = update_results_free(days_back=4)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    history = load_results_history()
    completed = [v for v in history.values() if v.get("completed")]
    print(f"\nTotal completed in history: {len(completed)}")
    for r in completed:
        print(f"  {r['home_team']} {r['home_score']}-{r['away_score']} {r['away_team']}")
