"""
Track the performance of our picks if a user followed them.

Storage:
  data/picks_history.json — keyed by event_id, each event holds the
    {safe, risky, ultra_risky} pick snapshot last saved by analyse_today().
  data/results_history.json — final scores fetched via /scores.

Evaluation:
  For h2h/totals picks: deterministic — we know if the bet won from the
  final score. For buteur picks: not derivable from /scores (the API does
  not return goalscorers). These stay flagged as "manual" until the user
  marks them won/lost via the UI, which writes a small overlay file.

Returns aggregate stats per category and a flat list of evaluated picks
for the performance tab.
"""

from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BE_TZ = ZoneInfo("Europe/Brussels")

DATA_DIR = Path(__file__).parent.parent / "data"
PICKS_PATH = DATA_DIR / "picks_history.json"
MANUAL_PATH = DATA_DIR / "manual_outcomes.json"
from fetch_results import RESULTS_PATH

# Stakes mirror the categories in bet_selector.
STAKES = {"safe": 10.0, "risky": 8.0, "ultra_risky": 2.0}
CATEGORY_LABELS = {"safe": "🛡️ Safe", "risky": "⚡ Risqué", "ultra_risky": "🎲 Ultra-risqué"}


# ---------- storage ----------

def load_picks_history() -> dict:
    if not PICKS_PATH.exists():
        return {}
    with open(PICKS_PATH, "r") as f:
        return json.load(f)


def save_picks_history(history: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(PICKS_PATH, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def load_results_history() -> dict:
    if not RESULTS_PATH.exists():
        return {}
    with open(RESULTS_PATH, "r") as f:
        return json.load(f)


def load_manual_outcomes() -> dict:
    if not MANUAL_PATH.exists():
        return {}
    with open(MANUAL_PATH, "r") as f:
        return json.load(f)


def save_manual_outcome(event_id: str, category: str, outcome: str) -> None:
    """outcome ∈ {'won', 'lost', 'refund', 'pending'}.

    'refund' = leg voided (e.g. buteur pick where the player didn't play).
    In a combo, refunded legs are treated as cote=1.0: combo wins on the
    product of non-refunded legs; if every leg is refunded, the whole
    combo is refunded (stake returned, P/L = 0).
    """
    if outcome not in ("won", "lost", "refund", "pending"):
        raise ValueError(f"invalid outcome: {outcome}")
    data = load_manual_outcomes()
    data.setdefault(event_id, {})[category] = outcome
    MANUAL_PATH.parent.mkdir(exist_ok=True)
    with open(MANUAL_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def record_picks(matches: list) -> None:
    """
    Persist today's picks into the history file (keyed by event_id).

    Past picks are immutable: once a match has kicked off, we never
    overwrite its picks. This protects the performance log against later
    re-runs of the pipeline (test sessions, code changes, manual reruns)
    that would otherwise rewrite history with different picks than the
    user actually saw on the live page when they took their bets.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    history = load_picks_history()
    for m in matches:
        event_id = m.get("event_id")
        if not event_id:
            continue
        # Lock picks for matches that have already started
        try:
            kickoff = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
            if kickoff <= now and event_id in history:
                continue
        except (ValueError, KeyError):
            pass

        entry = history.get(event_id, {})
        entry.update({
            "event_id": event_id,
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "commence_time": m["commence_time"],
        })
        for category in ("safe", "risky", "ultra_risky"):
            pick = m.get(category)
            if pick is None:
                continue
            entry[category] = {
                "market": pick["market"],
                "cote": pick["unibet_odd"],
                "fair_prob": pick["fair_prob"],
                "edge": pick["edge"],
                "has_value": pick.get("has_value", False),
                "is_buteur": pick.get("is_buteur", False),
            }
        history[event_id] = entry
    save_picks_history(history)


# ---------- evaluation ----------

def evaluate_pick(market: str, home_score: int, away_score: int) -> str:
    """
    Returns 'won' | 'lost' | 'refund' for any deterministic market.
    Raises ValueError for markets that need manual marking (scorer_*).
    Covers every market type the deep-rework selector can emit:
      h2h, double-chance, draw-no-bet, BTTS, match totals (over/under),
      team-specific totals.
    """
    if market == "h2h_home":
        return "won" if home_score > away_score else "lost"
    if market == "h2h_away":
        return "won" if away_score > home_score else "lost"
    if market == "h2h_draw":
        return "won" if home_score == away_score else "lost"
    if market == "dc_1X":
        return "won" if home_score >= away_score else "lost"
    if market == "dc_X2":
        return "won" if home_score <= away_score else "lost"
    if market == "dc_12":
        return "won" if home_score != away_score else "lost"
    if market == "dnb_home":
        if home_score == away_score:
            return "refund"
        return "won" if home_score > away_score else "lost"
    if market == "dnb_away":
        if home_score == away_score:
            return "refund"
        return "won" if away_score > home_score else "lost"
    if market == "btts_yes":
        return "won" if (home_score >= 1 and away_score >= 1) else "lost"
    if market == "btts_no":
        return "won" if not (home_score >= 1 and away_score >= 1) else "lost"
    if market.startswith("over_"):
        line = float(market.split("_")[1])
        return "won" if (home_score + away_score) > line else "lost"
    if market.startswith("under_"):
        line = float(market.split("_")[1])
        return "won" if (home_score + away_score) < line else "lost"
    if market.startswith("team_home_over_"):
        line = float(market.split("_")[-1])
        return "won" if home_score > line else "lost"
    if market.startswith("team_home_under_"):
        line = float(market.split("_")[-1])
        return "won" if home_score < line else "lost"
    if market.startswith("team_away_over_"):
        line = float(market.split("_")[-1])
        return "won" if away_score > line else "lost"
    if market.startswith("team_away_under_"):
        line = float(market.split("_")[-1])
        return "won" if away_score < line else "lost"
    raise ValueError(f"auto-eval not supported for market: {market}")


def evaluate_all() -> dict:
    """
    Join picks × results, return:
      {
        "rows": [{event_id, kickoff, home, away, category, market, cote,
                  stake, outcome, profit, status, result_text}, ...],
        "totals": {category: {n, wins, losses, pending, stake_total,
                              payout_total, profit, roi}}
      }
    """
    picks = load_picks_history()
    results = load_results_history()
    manual = load_manual_outcomes()

    rows = []
    totals = defaultdict(lambda: {
        "n": 0, "wins": 0, "losses": 0, "pending": 0,
        "stake_total": 0.0, "payout_total": 0.0, "profit": 0.0,
    })

    for event_id, entry in picks.items():
        result = results.get(event_id)
        for category in ("safe", "risky", "ultra_risky"):
            pick = entry.get(category)
            if not pick:
                continue
            stake = STAKES[category]
            market = pick["market"]
            cote = pick["cote"]
            outcome = "pending"
            result_text = ""

            # Manual overlay wins
            man_outcome = manual.get(event_id, {}).get(category)

            if pick.get("is_buteur") or market.startswith("scorer_"):
                if man_outcome:
                    outcome = man_outcome
                else:
                    outcome = "manual_pending"
            elif result and result.get("completed"):
                hs, as_ = result.get("home_score"), result.get("away_score")
                if hs is not None and as_ is not None:
                    try:
                        outcome = evaluate_pick(market, hs, as_)
                        result_text = f"{hs}-{as_}"
                    except ValueError:
                        outcome = "manual_pending"
                else:
                    outcome = "pending"
            else:
                outcome = "pending"

            if outcome == "won":
                payout = stake * cote
                profit = payout - stake
            elif outcome == "lost":
                payout = 0.0
                profit = -stake
            elif outcome == "refund":
                # Draw-no-bet pushed → stake refunded, zero profit/loss
                payout = stake
                profit = 0.0
            else:
                payout = 0.0
                profit = 0.0

            row = {
                "event_id": event_id,
                "kickoff": entry["commence_time"],
                "home": entry["home_team"],
                "away": entry["away_team"],
                "category": category,
                "market": market,
                "cote": cote,
                "stake": stake,
                "outcome": outcome,  # won, lost, pending, manual_pending
                "profit": profit,
                "result_text": result_text,
                "is_buteur": pick.get("is_buteur", False),
                "has_value": pick.get("has_value", False),
                "edge": pick.get("edge", 0),
            }
            rows.append(row)

            # Aggregate
            agg = totals[category]
            agg["n"] += 1
            if outcome == "won":
                agg["wins"] += 1
                agg["stake_total"] += stake
                agg["payout_total"] += stake * cote
                agg["profit"] += profit
            elif outcome == "lost":
                agg["losses"] += 1
                agg["stake_total"] += stake
                agg["profit"] += profit
            elif outcome == "refund":
                agg["stake_total"] += stake
            else:
                agg["pending"] += 1

    # Compute ROI per category
    for cat, agg in totals.items():
        agg["roi"] = (agg["profit"] / agg["stake_total"]) if agg["stake_total"] > 0 else 0.0
        agg["hit_rate"] = (agg["wins"] / (agg["wins"] + agg["losses"])) if (agg["wins"] + agg["losses"]) > 0 else 0.0

    rows.sort(key=lambda r: r["kickoff"], reverse=True)
    return {"rows": rows, "totals": dict(totals)}


# ---------- tournament context (group standings derived from results) ----------

def tournament_standings(group_definitions: dict | None = None) -> dict:
    """
    Compute W/D/L, goals for/against, points per team from completed matches.
    Returns {team: {gp, w, d, l, gf, ga, gd, pts}} sorted by points desc.
    """
    results = load_results_history()
    table = defaultdict(lambda: {"gp": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "gd": 0, "pts": 0})

    for ev_id, r in results.items():
        if not r.get("completed"):
            continue
        hs, as_ = r.get("home_score"), r.get("away_score")
        if hs is None or as_ is None:
            continue
        home, away = r["home_team"], r["away_team"]
        table[home]["gp"] += 1
        table[away]["gp"] += 1
        table[home]["gf"] += hs
        table[home]["ga"] += as_
        table[away]["gf"] += as_
        table[away]["ga"] += hs
        if hs > as_:
            table[home]["w"] += 1; table[home]["pts"] += 3
            table[away]["l"] += 1
        elif hs < as_:
            table[away]["w"] += 1; table[away]["pts"] += 3
            table[home]["l"] += 1
        else:
            table[home]["d"] += 1; table[home]["pts"] += 1
            table[away]["d"] += 1; table[away]["pts"] += 1
    for t, row in table.items():
        row["gd"] = row["gf"] - row["ga"]
    # Sort
    return dict(sorted(table.items(), key=lambda kv: (-kv[1]["pts"], -kv[1]["gd"], -kv[1]["gf"])))


def belgian_day_of(iso: str) -> str:
    """Belgian-window day key: matches kicking off 15h–05h59 next day group
    together under the SAME Belgian day (the one whose 15h started the window)."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BE_TZ)
    if dt.hour < 6:
        return (dt.date() - timedelta(days=1)).isoformat()
    return dt.date().isoformat()


def evaluate_combos(rows: list) -> list:
    """
    Aggregate individual pick outcomes into per-Belgian-day combo outcomes.
    The user actually plays the COMBINED bet of all picks in a category for
    a given day. So P/L and ROI are computed on the combo, not on legs.

    Combo outcome rule (industry-standard refund handling):
      - any leg lost     → combo LOST            P/L = −stake
      - any leg pending  → combo PENDING         P/L = 0
      - any leg manual_pending → MANUAL_PENDING  P/L = 0
      - every leg refunded     → combo REFUND    P/L = 0 (stake back)
      - all legs won OR refunded (≥1 won) → WON  P/L = stake × ∏(cotes of WON legs) − stake
        (refunded legs collapse to cote=1.0)
    """
    by_day_cat = defaultdict(lambda: defaultdict(list))
    for r in rows:
        bday = belgian_day_of(r["kickoff"])
        by_day_cat[bday][r["category"]].append(r)

    combos = []
    for bday in sorted(by_day_cat):
        for category in ("safe", "risky", "ultra_risky"):
            picks = by_day_cat[bday].get(category, [])
            if not picks:
                continue
            stake = STAKES[category]
            # Natural product (informational — what the combo would pay if
            # every leg won)
            cote = 1.0
            for p in picks:
                cote *= p["cote"]

            outcomes = [p["outcome"] for p in picks]
            if "lost" in outcomes:
                co = "lost"
                profit = -stake
            elif "pending" in outcomes:
                co = "pending"
                profit = 0.0
            elif "manual_pending" in outcomes:
                co = "manual_pending"
                profit = 0.0
            elif all(o == "refund" for o in outcomes):
                co = "refund"
                profit = 0.0
            else:
                # at least one won, possibly some refunded → product of WON legs
                effective_cote = 1.0
                for p in picks:
                    if p["outcome"] == "won":
                        effective_cote *= p["cote"]
                co = "won"
                profit = stake * effective_cote - stake

            combos.append({
                "belgian_day": bday,
                "category": category,
                "n_picks": len(picks),
                "cote_combined": cote,
                "stake": stake,
                "outcome": co,
                "profit": profit,
                "legs": picks,
            })
    return combos


def aggregate_combos(combos: list) -> dict:
    totals = defaultdict(lambda: {
        "n_combos": 0, "wins": 0, "losses": 0, "pending": 0,
        "stake_total": 0.0, "profit": 0.0,
    })
    for c in combos:
        t = totals[c["category"]]
        t["n_combos"] += 1
        if c["outcome"] == "won":
            t["wins"] += 1
            t["stake_total"] += c["stake"]
            t["profit"] += c["profit"]
        elif c["outcome"] == "lost":
            t["losses"] += 1
            t["stake_total"] += c["stake"]
            t["profit"] += c["profit"]
        else:
            t["pending"] += 1
    for cat, t in totals.items():
        decided = t["wins"] + t["losses"]
        t["roi"] = (t["profit"] / t["stake_total"]) if t["stake_total"] > 0 else 0.0
        t["hit_rate"] = (t["wins"] / decided) if decided > 0 else 0.0
    return dict(totals)


if __name__ == "__main__":
    out = evaluate_all()
    print(f"Evaluated picks: {len(out['rows'])}")
    for cat, t in out["totals"].items():
        print(f"  {cat:12s} n={t['n']:3d} W={t['wins']:3d} L={t['losses']:3d} pend={t['pending']:3d} "
              f"P/L={t['profit']:+8.2f}€ ROI={t['roi']*100:+.1f}%")
    print()
    standings = tournament_standings()
    print(f"Classement (équipes ayant joué au moins 1 match): {len(standings)}")
    for team, row in list(standings.items())[:8]:
        print(f"  {team:25s} GP {row['gp']} W{row['w']} D{row['d']} L{row['l']}  GF/GA {row['gf']}-{row['ga']}  GD {row['gd']:+d}  Pts {row['pts']}")
