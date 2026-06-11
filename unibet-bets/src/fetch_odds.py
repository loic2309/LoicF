"""
Fetch World Cup odds from The Odds API (EU region, all bookmakers).

API reality check (run on 2026-06-10):
  Unibet on The Odds API exposes ONLY h2h and totals — no BTTS, no spreads,
  no half-time markets, no player props. So we focus the betting product on
  these two markets, but still pull all EU bookmakers so we can compute a
  market-consensus probability (de-vigged median) for cross-validation.

Budget cost: 2 markets × 1 region = 2 credits per /odds call.
Strategy: fetch once per day, cache per-day. ~60 credits over the full
tournament, leaving a comfortable safety margin in the 500-credit budget.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def _load_env_file() -> None:
    """Lightweight .env loader (no python-dotenv dependency)."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


_load_env_file()

API_KEY = os.environ.get("ODDS_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "ODDS_API_KEY missing. Copy .env.example to .env and set your key, "
        "or export ODDS_API_KEY in your shell."
    )

SPORT_KEY = "soccer_fifa_world_cup"
BASE_URL = "https://api.the-odds-api.com/v4"

MARKETS = ["h2h", "totals"]
REGIONS = "eu"

# Unibet variants that surface on the EU feed
UNIBET_KEYS = {"unibet", "unibet_eu", "unibet_fr", "unibet_nl", "unibet_se", "unibet_it"}

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_DIR = DATA_DIR / "odds_cache"


def _http_get(url: str) -> tuple[dict, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "wc2026-bets/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        headers = {
            "remaining": resp.headers.get("x-requests-remaining"),
            "used": resp.headers.get("x-requests-used"),
            "last_cost": resp.headers.get("x-requests-last"),
        }
        body = json.loads(resp.read().decode("utf-8"))
    return body, headers


def fetch_bulk_odds() -> dict:
    """One call: h2h + totals for all upcoming WC events, region=eu."""
    params = {
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": ",".join(MARKETS),
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    url = f"{BASE_URL}/sports/{SPORT_KEY}/odds?{urllib.parse.urlencode(params)}"
    body, headers = _http_get(url)
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "remaining_credits": headers["remaining"],
        "used_credits": headers["used"],
        "last_call_cost": headers["last_cost"],
        "events": body,
    }


def cache_path_for_today() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return CACHE_DIR / f"odds_{today}.json"


def fetch_and_cache(force: bool = False) -> dict:
    path = cache_path_for_today()
    if path.exists() and not force:
        with open(path) as f:
            return json.load(f)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = fetch_bulk_odds()
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return payload


def extract_event_odds(event: dict) -> dict:
    """
    For a single event, extract:
      - unibet_odds: dict {market_label -> decimal odd} from any Unibet variant
        (picks the best/highest odd if multiple Unibet variants list the same market)
      - consensus_odds: dict {market_label -> consensus decimal odd} computed as
        the median odd across all EU bookmakers
    market_label keys match what the model uses (h2h_home/draw/away, over_X, under_X).
    """
    unibet_per_market = {}      # market_label -> list of Unibet odds
    consensus_per_market = {}   # market_label -> list of all-book odds

    home_name = event["home_team"]
    away_name = event["away_team"]

    for book in event.get("bookmakers", []):
        is_unibet = book.get("key", "") in UNIBET_KEYS
        for market in book.get("markets", []):
            mkey = market["key"]
            for o in market.get("outcomes", []):
                price = o.get("price")
                if not price or price <= 1.0:
                    continue
                label = None
                if mkey == "h2h":
                    if o["name"] == home_name:
                        label = "h2h_home"
                    elif o["name"] == away_name:
                        label = "h2h_away"
                    elif o["name"] == "Draw":
                        label = "h2h_draw"
                elif mkey == "totals":
                    pt = o.get("point")
                    if pt is None:
                        continue
                    pt_fmt = f"{pt:.1f}"
                    side = "over" if o["name"] == "Over" else "under"
                    label = f"{side}_{pt_fmt}"
                if label is None:
                    continue
                consensus_per_market.setdefault(label, []).append(price)
                if is_unibet:
                    unibet_per_market.setdefault(label, []).append(price)

    unibet_odds = {k: max(v) for k, v in unibet_per_market.items()}
    consensus_odds = {k: median(v) for k, v in consensus_per_market.items()}
    return {"unibet": unibet_odds, "consensus": consensus_odds}


def devigged_probs_from_odds(odds_by_outcome: dict[str, float]) -> dict[str, float]:
    """
    Convert a dict of decimal odds for a market into devigged probabilities
    (remove bookmaker margin). Uses the simple proportional method:
        p_i = (1/o_i) / sum_j(1/o_j)
    Returns the same keys with probability values.
    """
    raw = {k: 1.0 / o for k, o in odds_by_outcome.items() if o > 1.0}
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in raw.items()}


ADVANCED_MARKETS = ["btts", "double_chance", "alternate_totals", "team_totals", "player_goal_scorer_anytime"]


def fetch_advanced_odds(event_id: str) -> dict:
    """
    Per-event call for advanced markets (BTTS, double chance, alternate
    totals, team totals, anytime scorer). Cost: 5 credits per event when
    all 5 markets are returned (1 per market × 1 region). Cached per event
    so a refresh on the same day costs nothing.

    Returns a structured dict:
      {
        "btts": {"yes": odd, "no": odd},
        "dc":   {"1X": odd, "X2": odd, "12": odd},
        "alt_totals": {"over_0.5": odd, "under_0.5": odd, ...},
        "team_home_totals": {"over_0.5": odd, ...},
        "team_away_totals": {"over_0.5": odd, ...},
        "scorers": {"Player Name": odd, ...},
        "_remaining": "...",
      }
    Per-market: median across all books that quoted that line.
    """
    import urllib.error
    from collections import defaultdict
    params = {
        "apiKey": API_KEY,
        "regions": "eu,uk",
        "markets": ",".join(ADVANCED_MARKETS),
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    url = f"{BASE_URL}/sports/{SPORT_KEY}/events/{event_id}/odds?{urllib.parse.urlencode(params)}"
    try:
        body, headers = _http_get(url)
    except urllib.error.HTTPError as e:
        if e.code in (404, 422):
            return {"_remaining": e.headers.get("x-requests-remaining")}
        raise
    except Exception:
        return {}

    home = body.get("home_team")
    away = body.get("away_team")
    by_market = defaultdict(lambda: defaultdict(list))  # market_key -> outcome_label -> [prices]

    for book in body.get("bookmakers", []):
        for market in book.get("markets", []):
            mkey = market.get("key")
            for o in market.get("outcomes", []):
                price = o.get("price")
                if not price or price <= 1.0:
                    continue
                name = o.get("name", "")
                point = o.get("point")
                desc = o.get("description")  # player name for scorer markets

                if mkey == "btts":
                    if name == "Yes":
                        by_market["btts"]["yes"].append(price)
                    elif name == "No":
                        by_market["btts"]["no"].append(price)

                elif mkey == "double_chance":
                    # Outcome names vary: "Home/Draw", "Draw/Away", "Home/Away" or "1X", "X2", "12"
                    nm = name.lower()
                    if nm in ("home or draw", "home/draw", "1x", "1 or x") or (home and home.lower() in nm and "draw" in nm):
                        by_market["dc"]["1X"].append(price)
                    elif nm in ("draw or away", "draw/away", "x2", "x or 2") or (away and away.lower() in nm and "draw" in nm):
                        by_market["dc"]["X2"].append(price)
                    elif nm in ("home or away", "home/away", "12", "1 or 2"):
                        by_market["dc"]["12"].append(price)

                elif mkey == "alternate_totals":
                    if point is None: continue
                    label = "over" if name == "Over" else "under" if name == "Under" else None
                    if label is None: continue
                    by_market["alt_totals"][f"{label}_{point:.1f}"].append(price)

                elif mkey == "team_totals":
                    if point is None: continue
                    # The outcome carries the team name in 'description', side in 'name' (Over/Under)
                    side = "over" if name == "Over" else "under" if name == "Under" else None
                    if side is None: continue
                    team_label = desc or ""
                    if home and team_label == home:
                        by_market["team_home_totals"][f"{side}_{point:.1f}"].append(price)
                    elif away and team_label == away:
                        by_market["team_away_totals"][f"{side}_{point:.1f}"].append(price)

                elif mkey == "player_goal_scorer_anytime":
                    player = desc or name
                    by_market["scorers"][player].append(price)

    # Reduce: take the median across books for each (market, outcome)
    result = {}
    for mkey, outcomes in by_market.items():
        result[mkey] = {label: median(prices) for label, prices in outcomes.items() if prices}
    result["_remaining"] = headers.get("remaining")
    return result


def fetch_player_goalscorer_odds(event_id: str) -> dict:
    """
    Per-event call for player_goal_scorer_anytime. Returns {player_name:
    {"odd": median_decimal_odd, "n_books": int}}. Unibet does not expose
    this market via the API — odds come from Pinnacle / William Hill /
    Sky Bet / Betfair. The user should verify final cote on unibet.be.
    Cost: 1 credit per call.
    """
    params = {
        "apiKey": API_KEY,
        "regions": "eu",  # Pinnacle (best devig) is in EU; one region keeps cost low
        "markets": "player_goal_scorer_anytime",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    url = f"{BASE_URL}/sports/{SPORT_KEY}/events/{event_id}/odds?{urllib.parse.urlencode(params)}"
    try:
        body, headers = _http_get(url)
    except Exception:
        return {"_remaining": None}
    by_player = {}
    for book in body.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "player_goal_scorer_anytime":
                continue
            for o in market.get("outcomes", []):
                name = o.get("description") or o.get("name")
                price = o.get("price")
                if not name or not price or price <= 1.0:
                    continue
                by_player.setdefault(name, []).append(price)
    result = {name: {"odd": median(prices), "n_books": len(prices)}
              for name, prices in by_player.items() if len(prices) >= 1}
    result["_remaining"] = headers.get("remaining")
    result["_used"] = headers.get("used")
    return result


def cache_buteur_path_for_event(event_id: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return CACHE_DIR / f"buteur_{today}_{event_id}.json"


def cache_advanced_path_for_event(event_id: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return CACHE_DIR / f"advanced_{today}_{event_id}.json"


def fetch_advanced_for_today(event_ids: list, force: bool = False) -> dict:
    """Per-event fetch of advanced markets (BTTS, DC, alt totals, team totals,
    scorers) for today's events, cached per-event-per-day."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    last_remaining = None
    for ev_id in event_ids:
        path = cache_advanced_path_for_event(ev_id)
        if path.exists() and not force:
            with open(path) as f:
                out[ev_id] = json.load(f)
            continue
        data = fetch_advanced_odds(ev_id)
        if "_remaining" in data and data["_remaining"]:
            last_remaining = data["_remaining"]
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        out[ev_id] = data
    out["_last_remaining"] = last_remaining
    return out


def fetch_buteur_for_today(today_event_ids: list, force: bool = False) -> dict:
    """
    Fetch player goalscorer odds for today's events only. Caches per event.
    Returns {event_id: {player_name: {odd, n_books}}, "_last_remaining": int|None}.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    last_remaining = None
    for ev_id in today_event_ids:
        path = cache_buteur_path_for_event(ev_id)
        if path.exists() and not force:
            with open(path) as f:
                out[ev_id] = json.load(f)
            continue
        data = fetch_player_goalscorer_odds(ev_id)
        if "_remaining" in data and data["_remaining"]:
            last_remaining = data["_remaining"]
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        out[ev_id] = data
    out["_last_remaining"] = last_remaining
    return out


if __name__ == "__main__":
    force = "--force" in sys.argv
    payload = fetch_and_cache(force=force)
    print(f"Fetched at:        {payload['fetched_at']}")
    print(f"Credits used:      {payload['used_credits']} (this call cost {payload['last_call_cost']})")
    print(f"Credits remaining: {payload['remaining_credits']}")
    print(f"Events:            {len(payload['events'])}")
