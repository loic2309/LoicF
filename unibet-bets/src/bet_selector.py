"""
Pick a safe / risky / ultra-risky bet per match for today's Belgian window,
then assemble three daily combos with their respective caps.

Categories
----------
  safe        — prob ≥ 0.50, max-edge outcome among h2h/totals
                Stake 10 €. Combo cap on cote totale: 5.0
  risky       — 0.18 ≤ prob < 0.50, max-edge, compatible with safe
                Stake 8 €. Combo cap on cote totale: 25.0
  ultra_risky — buteur "anytime" pick from the favorite team's top scorer
                pool. Falls back to a long-shot h2h/totals outcome
                (prob < 0.25) if buteur odds are unavailable.
                Stake 2 €. No combo cap.

Belgian day window
------------------
A "Belgian day" is the window [today 15:00, tomorrow 06:00] in
Europe/Brussels. Matches whose kickoff falls in that window are the only
ones surfaced. Before 06:00 local time we still serve the window that
started yesterday at 15:00.

Combo caps
----------
Each daily combo is built as follows:
  1. Compute the natural product of all picks of that category for the day.
  2. If it respects the cap, keep all picks.
  3. Otherwise, brute-force across subsets (≤ ~10 picks/day, fits easily)
     to find the subset whose combo product is the highest value ≤ cap.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

from model import predict_match, load_elo
from fetch_odds import (
    fetch_and_cache,
    fetch_buteur_for_today,
    fetch_advanced_for_today,
    fetch_current_credits,
    extract_event_odds,
    devigged_probs_from_odds,
    UNIBET_KEYS,
)
from team_codes import TEAM_TO_CODE
from team_form import load_elo_full, get_key_players
from tournament_form import combined_form_data
from players import scorers_for, player_score_prob
from insights import generate as generate_insight
from performance import record_picks

DATA_DIR = Path(__file__).parent.parent / "data"
BE_TZ = ZoneInfo("Europe/Brussels")

MODEL_WEIGHT = 0.40
CONSENSUS_WEIGHT = 0.60

# Sanity cap: when both model and market consensus are available, our final
# "fair probability" cannot exceed 1.3× the consensus probability. This is a
# common-sense guardrail: if the model thinks an outcome is twice as likely
# as the market does, the model is almost certainly missing context the
# market has (injury news, motivation, lineup intel, etc.). We trust the
# market over a divergent model on the upside. The cap never *raises* a
# probability — only prevents runaway over-confidence relative to market.
MAX_MODEL_OVER_MARKET = 1.30

# Hard edge ceiling. Realistic positive-EV opportunities at modest +3-15%
# happen; an apparent +50%+ edge almost always reflects model bias rather
# than genuine inefficiency (the market is well-calibrated on extreme cotes
# because heavy money flows through it). When the raw edge crosses this
# ceiling we clamp it — and back-derive a fair probability consistent with
# that ceiling — so the combo math stays internally consistent.
MAX_EDGE_CEILING = 0.30


def cap_edge(fair_prob: float, odd: float) -> tuple[float, float]:
    """If (fair_prob * odd - 1) > MAX_EDGE_CEILING, lower fair_prob so the
    edge sits exactly at the ceiling. Returns (capped_fair_prob, capped_edge).
    Never raises a probability."""
    raw_edge = fair_prob * odd - 1.0
    if raw_edge <= MAX_EDGE_CEILING:
        return fair_prob, raw_edge
    capped_fair = (1.0 + MAX_EDGE_CEILING) / odd
    return capped_fair, MAX_EDGE_CEILING

# Probability bands
SAFE_MIN_PROB = 0.50
RISKY_MIN_PROB = 0.18
RISKY_MAX_PROB = 0.50

# Hard caps on individual pick cotes per category. The combo caps (5/25)
# are on the product; these caps are on each leg so the labels stay honest:
#   - safe leg cote ≤ 2.00 (implied market prob ≥ 50%)
#   - risky leg cote ≤ 4.50 (above that, the bet belongs in ultra)
SAFE_MAX_INDIVIDUAL_ODD = 2.00
RISKY_MAX_INDIVIDUAL_ODD = 4.50

# Tie-break: among picks within this edge band of the top candidate, prefer
# h2h outcomes (Victoire X / Match nul) over derivative markets (BTTS, DC,
# alt totals). User feedback: h2h picks feel "more natural" and pair better
# with totals in combos — joint winning scores are more numerous.
H2H_TIE_BAND = 0.03  # 3 percentage points
H2H_MARKETS = {"h2h_home", "h2h_away", "h2h_draw"}

# Value flag thresholds (edge above which a pick earns the VALEUR badge)
SAFE_VALUE_EDGE = 0.02
RISKY_VALUE_EDGE = 0.05
ULTRA_VALUE_EDGE = 0.05

# Daily combo caps
SAFE_COMBO_CAP = 5.0
RISKY_COMBO_CAP = 25.0
# ultra-risky: no cap

# Stakes
SAFE_STAKE = 10.0
RISKY_STAKE = 8.0
ULTRA_STAKE = 2.0

HOST_NATIONS = {"USA", "Canada", "Mexico"}


def label_for(market_key: str, home: str, away: str) -> str:
    if market_key == "h2h_home":
        return f"Victoire {home}"
    if market_key == "h2h_away":
        return f"Victoire {away}"
    if market_key == "h2h_draw":
        return "Match nul"
    if market_key == "dc_1X":
        return f"{home} ou Nul (Double chance)"
    if market_key == "dc_X2":
        return f"Nul ou {away} (Double chance)"
    if market_key == "dc_12":
        return f"{home} ou {away} (pas de nul)"
    if market_key == "dnb_home":
        return f"{home} gagne (Nul remboursé)"
    if market_key == "dnb_away":
        return f"{away} gagne (Nul remboursé)"
    if market_key.startswith("over_"):
        return f"Plus de {market_key.split('_')[1]} buts (match)"
    if market_key.startswith("under_"):
        return f"Moins de {market_key.split('_')[1]} buts (match)"
    if market_key == "btts_yes":
        return "Les deux équipes marquent"
    if market_key == "btts_no":
        return "Au moins une équipe ne marque pas"
    if market_key.startswith("team_home_over_"):
        return f"{home} marque + de {market_key.split('_')[-1]} buts"
    if market_key.startswith("team_home_under_"):
        return f"{home} marque − de {market_key.split('_')[-1]} buts"
    if market_key.startswith("team_away_over_"):
        return f"{away} marque + de {market_key.split('_')[-1]} buts"
    if market_key.startswith("team_away_under_"):
        return f"{away} marque − de {market_key.split('_')[-1]} buts"
    if market_key.startswith("scorer_"):
        return f"{market_key[len('scorer_'):]} buteur"
    return market_key


# Score-matrix compatibility: enumerate all reasonable final scores and check
# that at least one makes BOTH bets pay. Robust for any market combination.
def _bet_wins(market: str, h: int, a: int) -> bool:
    if market == "h2h_home": return h > a
    if market == "h2h_draw": return h == a
    if market == "h2h_away": return a > h
    if market == "dc_1X": return h >= a
    if market == "dc_X2": return h <= a
    if market == "dc_12": return h != a
    if market == "dnb_home": return h > a  # draw refunds (treated as not-winning here)
    if market == "dnb_away": return a > h
    if market.startswith("over_"):
        return (h + a) > float(market.split("_")[1])
    if market.startswith("under_"):
        return (h + a) < float(market.split("_")[1])
    if market == "btts_yes": return h >= 1 and a >= 1
    if market == "btts_no": return not (h >= 1 and a >= 1)
    if market.startswith("team_home_over_"):
        return h > float(market.split("_")[-1])
    if market.startswith("team_home_under_"):
        return h < float(market.split("_")[-1])
    if market.startswith("team_away_over_"):
        return a > float(market.split("_")[-1])
    if market.startswith("team_away_under_"):
        return a < float(market.split("_")[-1])
    if market.startswith("scorer_"): return True  # independent of score
    if market.startswith("ah_"): return True  # not used in picks today
    return True  # unknown → don't block


def belgian_day_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Returns the (start, end) Belgian-time window for today's slate."""
    if now is None:
        now = datetime.now(BE_TZ)
    else:
        now = now.astimezone(BE_TZ)
    if now.hour < 6:
        start = now.replace(hour=15, minute=0, second=0, microsecond=0) - timedelta(days=1)
    else:
        start = now.replace(hour=15, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=15)
    return start, end


def consensus_probs_for_event(consensus_odds: dict) -> dict:
    probs = {}
    h2h_market = {k: v for k, v in consensus_odds.items() if k.startswith("h2h_")}
    if h2h_market:
        probs.update(devigged_probs_from_odds(h2h_market))
    totals_lines = set()
    for k in consensus_odds:
        if k.startswith("over_") or k.startswith("under_"):
            totals_lines.add(k.split("_")[1])
    for line in totals_lines:
        pair = {
            f"over_{line}": consensus_odds.get(f"over_{line}", 0),
            f"under_{line}": consensus_odds.get(f"under_{line}", 0),
        }
        pair = {k: v for k, v in pair.items() if v > 1.0}
        if len(pair) == 2:
            probs.update(devigged_probs_from_odds(pair))
    return probs


def _blend(m_prob, c_prob):
    """Blend model + consensus into a fair prob, with sanity cap."""
    if c_prob is None:
        return m_prob
    if m_prob is None:
        return c_prob
    blended = MODEL_WEIGHT * m_prob + CONSENSUS_WEIGHT * c_prob
    return min(blended, MAX_MODEL_OVER_MARKET * c_prob)


def evaluate_outcomes(model_probs: dict, consensus_probs: dict, unibet_odds: dict,
                       advanced_odds: dict | None = None) -> list:
    """
    Build the full candidate row list across every market we have data for:
      - Unibet h2h + totals (primary cotes the user actually plays at)
      - Advanced markets (BTTS, DC, alt totals, team totals): cotes from
        median of EU/UK bookmakers (Unibet doesn't expose these on the API
        — user must verify on unibet.be before placing).
    Each row carries the source flag so the UI can mark "consensus cote" picks.
    """
    rows = []
    # Track which markets are sourced from Unibet vs consensus
    def add(mkey, odd, source, note=None):
        m_prob = model_probs.get(mkey)
        c_prob = consensus_probs.get(mkey)
        if m_prob is None and c_prob is None:
            return
        fair = _blend(m_prob, c_prob)
        fair, edge = cap_edge(fair, odd)
        row = {
            "market": mkey, "model_prob": m_prob, "consensus_prob": c_prob,
            "fair_prob": fair, "unibet_odd": odd, "edge": edge,
            "source": source,
        }
        if note:
            row["note"] = note
        rows.append(row)

    # Unibet primary odds (h2h_*, over_X, under_X)
    for mkey, odd in unibet_odds.items():
        add(mkey, odd, source="unibet")

    if not advanced_odds:
        return rows

    note_consensus = "Cote indicative (consensus marché). À vérifier sur unibet.be."

    # BTTS — both teams to score yes/no
    btts = advanced_odds.get("btts", {})
    if "yes" in btts: add("btts_yes", btts["yes"], "consensus", note_consensus)
    if "no" in btts:  add("btts_no",  btts["no"],  "consensus", note_consensus)

    # Double chance — fetched ad-hoc per event (not in the default
    # ADVANCED_MARKETS set for budget reasons). When present in cache,
    # unlocks "Home ou Nul" / "Nul ou Away" / "Home ou Away" picks.
    dc = advanced_odds.get("dc", {})
    if "1X" in dc: add("dc_1X", dc["1X"], "consensus", note_consensus)
    if "X2" in dc: add("dc_X2", dc["X2"], "consensus", note_consensus)
    if "12" in dc: add("dc_12", dc["12"], "consensus", note_consensus)

    # Scorers feed pick_ultra_risky separately (not part of the row pool).
    return rows


def are_compatible(market_a: str, market_b: str) -> bool:
    """Two markets are compatible iff there exists at least one final score
    (h, a) ∈ [0..7]² where both bets pay."""
    return jointly_compatible(market_a, market_b)


def jointly_compatible(*markets) -> bool:
    """N markets are jointly compatible iff there exists at least one
    final score where every one of them pays. Pairwise compatibility
    doesn't imply triple compatibility — e.g. Under 2.5 + BTTS yes +
    Victoire Paraguay are pairwise OK but jointly impossible (forces
    home=1, away=2, total=3 which violates Under 2.5)."""
    markets = [m for m in markets if m]
    # Scorer markets are independent of the final score (player either
    # scored or didn't, decoupled from h/a outcome). Strip them out.
    deterministic = [m for m in markets if not m.startswith("scorer_")]
    if not deterministic:
        return True
    if len(deterministic) == 1:
        return True
    if any(deterministic.count(m) > 1 for m in deterministic):
        # Same market repeated — trivially compatible
        deterministic = list(set(deterministic))
    for h in range(8):
        for a in range(8):
            if all(_bet_wins(m, h, a) for m in deterministic):
                return True
    return False


def _pick_with_h2h_preference(pool: list) -> dict | None:
    """Picks the max-edge candidate, but if h2h alternatives sit within
    H2H_TIE_BAND of that max edge, prefer the h2h one. Rationale: h2h
    outcomes (Victoire X / Nul) cover more winning final scores when
    paired with totals than derivative markets like BTTS — joint combo
    win probability is higher with h2h legs."""
    if not pool:
        return None
    top = max(pool, key=lambda r: r["edge"])
    h2h_candidates = [
        r for r in pool
        if r["market"] in H2H_MARKETS
        and r["edge"] >= top["edge"] - H2H_TIE_BAND
    ]
    if h2h_candidates:
        return max(h2h_candidates, key=lambda r: r["edge"])
    return top


def pick_safe_and_risky(rows: list) -> tuple[dict | None, dict | None]:
    safe_pool = [
        r for r in rows
        if r["fair_prob"] >= SAFE_MIN_PROB
        and r["unibet_odd"] <= SAFE_MAX_INDIVIDUAL_ODD
    ]
    risky_pool = [
        r for r in rows
        if RISKY_MIN_PROB <= r["fair_prob"] < RISKY_MAX_PROB
        and r["unibet_odd"] <= RISKY_MAX_INDIVIDUAL_ODD
    ]
    safe = _pick_with_h2h_preference(safe_pool)
    if safe is not None:
        risky_pool = [r for r in risky_pool if are_compatible(safe["market"], r["market"])]
    risky = _pick_with_h2h_preference(risky_pool)
    if safe is not None:
        safe["has_value"] = safe["edge"] >= SAFE_VALUE_EDGE
    if risky is not None:
        risky["has_value"] = risky["edge"] >= RISKY_VALUE_EDGE
    return safe, risky


ULTRA_MIN_ODD = 2.5  # for non-buteur picks, only cotes ≥ 2.5 count as ultra
ULTRA_MAX_PROB = 0.40


def pick_ultra_risky(
    home: str, away: str, lam_h: float, lam_a: float,
    buteur_odds: dict, model_probs: dict, unibet_odds: dict,
    consensus_probs: dict,
    exclude_markets: set | None = None,
    compat_with: list | None = None,
) -> dict | None:
    """
    Ultra-risky pool combines several long-shot market types and picks the
    single highest-edge candidate, regardless of which family it comes from:

      a) Player goalscorer anytime — top-3 forwards per team, scored by the
         Poisson player model (λ_team × scorer_share). Cote = consensus
         market median (non-Unibet — Unibet doesn't expose this market on
         the API; user verifies on unibet.be).

      b) Long-shot 1X2 — any 1X2 outcome with cote ≥ 2.5 and fair_prob in
         [0.10, 0.40]. Catches underdog wins and draws in close matches.

      c) Long-shot totals — Over/Under buts lines with cote ≥ 2.5 and
         fair_prob ≤ 0.40 (typical: Over 3.5/4.5 in big-favorite matches,
         Under 1.5 in defensive ones).

    "Fair prob" uses the same 40% model + 60% consensus blend as safe/risky
    when consensus is available, else the raw model probability.

    exclude_markets: set of market keys already used by safe/risky for this
    match. The ultra pick must come from a different market so the three
    categories don't all bet on the exact same outcome.

    compat_with: list of market keys (safe + risky picks) that the ultra
    pick must be jointly compatible with — i.e. there must exist at least
    one final score where all three bets pay simultaneously. Pairwise
    compatibility isn't enough.
    """
    exclude = exclude_markets or set()
    compat_with = compat_with or []
    candidates = []

    # (a) Buteur candidates. Sanity-check: the market often knows better than
    # our coarse hardcoded scorer shares (defenders vs strikers, role on
    # national team vs club, etc.). We cap our model probability at
    # 1.5 × market-implied probability — meaning we accept positive edge
    # only when the market also considers the player a plausible scorer.
    for team, lam in ((home, lam_h), (away, lam_a)):
        for player_name, share in scorers_for(team):
            model_p_raw = player_score_prob(lam, share)
            matched_odd = None
            for feed_name, info in buteur_odds.items():
                fn = feed_name.lower()
                pn = player_name.lower()
                if pn in fn or fn in pn:
                    matched_odd = info["odd"]
                    break
            if matched_odd is None:
                continue
            # Buteur consensus uses 1/cote (vig-inclusive) — slightly
            # pessimistic, so we use a looser 1.5× cap before the global
            # edge ceiling kicks in.
            implied_market = 1.0 / matched_odd
            fair_prob = min(model_p_raw, 1.5 * implied_market)
            fair_prob, edge = cap_edge(fair_prob, matched_odd)
            candidates.append({
                "market": f"scorer_{player_name}",
                "model_prob": model_p_raw,
                "consensus_prob": implied_market,
                "fair_prob": fair_prob,
                "unibet_odd": matched_odd,
                "edge": edge,
                "is_buteur": True,
                "team": team,
                "source": "consensus",
                "note": "Cote indicative (consensus marché, ~Pinnacle). À vérifier sur unibet.be.",
            })

    # (b)+(c) Long-shot 1X2 + totals at the Unibet cote.
    # Same _blend() sanity cap applies here (fair ≤ 1.3 × consensus), so
    # blowout draws and one-sided over/under picks can't keep an absurd
    # 90%+ edge derived from model-vs-market divergence alone.
    for mkey, odd in unibet_odds.items():
        if mkey in exclude:
            continue
        if odd < ULTRA_MIN_ODD:
            continue
        m_prob = model_probs.get(mkey)
        c_prob = consensus_probs.get(mkey)
        if m_prob is None and c_prob is None:
            continue
        fair = _blend(m_prob, c_prob)
        if fair > ULTRA_MAX_PROB:
            continue
        fair, edge = cap_edge(fair, odd)
        candidates.append({
            "market": mkey,
            "model_prob": m_prob,
            "consensus_prob": c_prob,
            "fair_prob": fair,
            "unibet_odd": odd,
            "edge": edge,
            "is_buteur": False,
            "source": "unibet" if mkey in ("h2h_home", "h2h_draw", "h2h_away") or mkey.startswith(("over_", "under_")) else "consensus",
        })

    # Exclude same-market repeats, then enforce JOINT compatibility with
    # safe + risky picks (not just pairwise).
    candidates = [c for c in candidates if c["market"] not in exclude]
    candidates = [
        c for c in candidates
        if jointly_compatible(c["market"], *compat_with)
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda r: r["edge"])
    best["has_value"] = best["edge"] >= ULTRA_VALUE_EDGE
    return best


# Edge above which an excluded pick gets surfaced as a "must-play" alert
MUST_PLAY_EDGE_THRESHOLD = 0.20


def find_must_play_alerts(
    home: str, away: str, lam_h: float, lam_a: float,
    buteur_odds: dict, model_probs: dict, unibet_odds: dict,
    consensus_probs: dict,
    chosen_markets: list,
) -> list:
    """
    Find picks with edge ≥ MUST_PLAY_EDGE_THRESHOLD that got excluded from
    the combo because they're incompatible with the chosen safe/risky/ultra
    picks. These are "play it solo if you have conviction" opportunities.
    """
    chosen_set = set(chosen_markets)
    candidates = []

    # h2h/totals/BTTS/DC/etc. — anything in the row pool with strong edge
    for mkey, odd in unibet_odds.items():
        m_prob = model_probs.get(mkey)
        c_prob = consensus_probs.get(mkey)
        if m_prob is None and c_prob is None:
            continue
        fair = _blend(m_prob, c_prob)
        fair, edge = cap_edge(fair, odd)
        if edge < MUST_PLAY_EDGE_THRESHOLD:
            continue
        if mkey in chosen_set:
            continue
        if jointly_compatible(mkey, *chosen_markets):
            continue  # fits the combo, not a "must-play simple"
        candidates.append({
            "market": mkey, "unibet_odd": odd, "fair_prob": fair, "edge": edge,
            "source": "unibet" if mkey in ("h2h_home","h2h_draw","h2h_away") or mkey.startswith(("over_","under_")) else "consensus",
        })

    # Buteur candidates with strong edge — but scorers are independent so
    # they're never "incompatible". Skip from must-play.

    return sorted(candidates, key=lambda c: -c["edge"])[:3]


def analyse_event(event: dict, elo: dict, form_data: dict,
                  buteur_odds: dict | None = None,
                  advanced_odds: dict | None = None) -> dict:
    home, away = event["home_team"], event["away_team"]
    if home not in elo or away not in elo:
        return None

    neutral = home not in HOST_NATIONS
    prediction = predict_match(home, away, elo, neutral_venue=neutral, form_data=form_data)
    odds = extract_event_odds(event)

    if not odds["unibet"] and not advanced_odds:
        return {
            "event_id": event["id"], "home_team": home, "away_team": away,
            "commence_time": event["commence_time"], "prediction": prediction,
            "skipped_reason": "no odds available", "safe": None,
            "risky": None, "ultra_risky": None, "outcomes": [],
        }

    consensus_probs = consensus_probs_for_event(odds["consensus"])
    rows = evaluate_outcomes(
        prediction["probs"], consensus_probs, odds["unibet"],
        advanced_odds=advanced_odds,
    )
    safe, risky = pick_safe_and_risky(rows)

    # Buteur picks still come from the dedicated buteur fetch (which only
    # pulls player_goal_scorer_anytime; redundant with advanced_odds.scorers
    # but kept for backward compat with already-cached files).
    scorer_odds_merged = dict(buteur_odds or {})
    if advanced_odds and "scorers" in advanced_odds:
        for name, odd in advanced_odds["scorers"].items():
            scorer_odds_merged.setdefault(name, {"odd": odd, "n_books": 1})

    # Build exclusion + joint-compatibility constraints so the three picks
    # can all pay out at the same final score (not just pairwise).
    exclude = set()
    compat_chain = []
    if safe is not None:
        exclude.add(safe["market"])
        compat_chain.append(safe["market"])
    if risky is not None:
        exclude.add(risky["market"])
        compat_chain.append(risky["market"])

    ultra_risky = pick_ultra_risky(
        home, away,
        prediction["lambda_home"], prediction["lambda_away"],
        scorer_odds_merged, prediction["probs"], odds["unibet"],
        consensus_probs,
        exclude_markets=exclude,
        compat_with=compat_chain,
    )

    # Surface high-edge picks that got dropped due to joint-compat
    chosen = list(compat_chain)
    if ultra_risky is not None:
        chosen.append(ultra_risky["market"])
    must_play = find_must_play_alerts(
        home, away,
        prediction["lambda_home"], prediction["lambda_away"],
        scorer_odds_merged, prediction["probs"], odds["unibet"],
        consensus_probs,
        chosen_markets=chosen,
    )
    if ultra_risky and safe and not are_compatible(safe["market"], ultra_risky["market"]):
        ultra_risky = None  # fallback path — shouldn't trigger because scorer is always compatible
    if ultra_risky and risky and not are_compatible(risky["market"], ultra_risky["market"]):
        ultra_risky = None

    result = {
        "event_id": event["id"], "home_team": home, "away_team": away,
        "commence_time": event["commence_time"], "prediction": prediction,
        "unibet_odds": odds["unibet"], "consensus_odds": odds["consensus"],
        "consensus_probs": consensus_probs, "outcomes": rows,
        "safe": safe, "risky": risky, "ultra_risky": ultra_risky,
        "form_home": (form_data or {}).get(home),
        "form_away": (form_data or {}).get(away),
        "key_players_home": get_key_players(home),
        "key_players_away": get_key_players(away),
        "buteur_odds_count": len(buteur_odds) if buteur_odds else 0,
    }
    result["must_play"] = must_play
    result["insight"] = generate_insight(result, form_data=form_data)
    return result


def best_subset_under_cap(picks: list, cap: float) -> list:
    """Return the subset of picks whose product of cotes is ≤ cap and
    maximum. With ≤ ~10 picks per day, brute-force enumeration is fine."""
    if not picks:
        return []
    natural = 1.0
    for p in picks:
        natural *= p["unibet_odd"]
    if natural <= cap:
        return picks

    n = len(picks)
    best = []
    best_product = 0.0
    # Enumerate non-empty subsets (skip empty since 1.0 is trivial)
    for k in range(1, n + 1):
        for combo in combinations(range(n), k):
            prod = 1.0
            for i in combo:
                prod *= picks[i]["unibet_odd"]
            if prod > cap:
                continue
            # Tie break: larger subset wins, then higher product
            if (
                prod > best_product
                or (prod == best_product and len(combo) > len(best))
            ):
                best = [picks[i] for i in combo]
                best_product = prod
    return best


def build_combo(matches: list, kind: str, cap: float | None) -> dict:
    """Aggregate today's picks of the given kind into a combo respecting cap."""
    picks_with_match = [(m, m.get(kind)) for m in matches if m.get(kind) is not None]
    if not picks_with_match:
        return {"kind": kind, "selections": [], "cote": 1.0, "proba": 1.0,
                "excluded": [], "n_total": 0, "n_selected": 0}

    only_picks = [p for _, p in picks_with_match]
    if cap is None:
        selected_picks = only_picks
    else:
        selected_picks = best_subset_under_cap(only_picks, cap)

    selected_ids = {id(p) for p in selected_picks}
    selections, excluded = [], []
    for m, p in picks_with_match:
        if id(p) in selected_ids:
            selections.append({"match": m, "pick": p})
        else:
            excluded.append({"match": m, "pick": p})

    cote = 1.0
    proba = 1.0
    for s in selections:
        cote *= s["pick"]["unibet_odd"]
        proba *= s["pick"]["fair_prob"]
    return {
        "kind": kind, "selections": selections, "excluded": excluded,
        "cote": cote, "proba": proba, "n_total": len(picks_with_match),
        "n_selected": len(selections),
    }


def analyse_today(force_buteur: bool = False) -> dict:
    """
    Analyse the matches in today's Belgian window [15:00 → next 06:00].
    If that window has no matches (e.g. on a rest day), automatically roll
    forward to the next window that contains at least one match, so the
    page never lands empty during the tournament.
    """
    payload = fetch_and_cache()
    elo = load_elo()
    base_form = load_elo_full()
    # Merge pre-tournament form with in-tournament results
    form_data = combined_form_data(elo, base_form)

    start, end = belgian_day_window()
    auto_rolled = False

    def events_in(s, e):
        ev_in = []
        for ev in payload["events"]:
            ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(BE_TZ)
            if s <= ko < e:
                ev_in.append(ev)
        return sorted(ev_in, key=lambda e: e["commence_time"])

    todays_events = events_in(start, end)
    # If today is empty, scan the next 30 days for the next non-empty window
    if not todays_events:
        cursor_start = start
        for _ in range(30):
            cursor_start += timedelta(days=1)
            cursor_end = cursor_start + timedelta(hours=15)
            candidate = events_in(cursor_start, cursor_end)
            if candidate:
                start, end = cursor_start, cursor_end
                todays_events = candidate
                auto_rolled = True
                break

    # Per-event fetch for today's matches: ONE bundle (BTTS + DC + alt
    # totals + scorers) per event, cached forever — re-fetch only when a
    # new event first enters the window. The legacy buteur-only call is
    # gone (scorers are inside the bundle).
    advanced_by_event = {}
    last_remaining_after_fetch = None
    if todays_events:
        ev_ids = [e["id"] for e in todays_events]
        advanced_by_event = fetch_advanced_for_today(ev_ids, force=force_buteur)
        last_remaining_after_fetch = advanced_by_event.pop("_last_remaining", None)

    results = []
    for ev in todays_events:
        ev_advanced = advanced_by_event.get(ev["id"], {})
        clean_advanced = {k: v for k, v in ev_advanced.items() if not k.startswith("_")}
        # Build scorer dict from the bundle so pick_ultra_risky still works
        scorer_odds = {name: {"odd": odd, "n_books": 1}
                       for name, odd in clean_advanced.get("scorers", {}).items()}
        r = analyse_event(ev, elo, form_data,
                          buteur_odds=scorer_odds,
                          advanced_odds=clean_advanced)
        if r is not None:
            results.append(r)

    # Build three combos with their caps
    safe_combo = build_combo(results, "safe", SAFE_COMBO_CAP)
    risky_combo = build_combo(results, "risky", RISKY_COMBO_CAP)
    ultra_combo = build_combo(results, "ultra_risky", None)

    # Snapshot the picks for performance tracking (overwrites prior snapshot
    # for the same event_id — last refresh of the day wins).
    if results:
        record_picks(results)

    # The cached value can be stale (multiple workflow runs, manual ops in
    # between). Hit /sports (FREE — doesn't count against quota) to refresh
    # the displayed counter at render time.
    fresh = fetch_current_credits()
    if fresh and fresh.get("remaining"):
        remaining = fresh["remaining"]
        used = fresh["used"] or str(500 - int(remaining))
    else:
        remaining = last_remaining_after_fetch or payload["remaining_credits"]
        used = (500 - int(remaining)) if remaining else payload["used_credits"]

    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "auto_rolled": auto_rolled,
        "fetched_at": payload["fetched_at"],
        "credits_used": used,
        "credits_remaining": remaining,
        "matches": results,
        "safe_combo": safe_combo,
        "risky_combo": risky_combo,
        "ultra_combo": ultra_combo,
    }


# Kept for backward compatibility — used by older render code paths if any
def analyse_all() -> dict:
    return analyse_today()


if __name__ == "__main__":
    import sys
    out = analyse_today(force_buteur="--force-buteur" in sys.argv)
    print(f"Window: {out['window_start']} → {out['window_end']}")
    print(f"Matches today: {len(out['matches'])}")
    print(f"Credits: used={out['credits_used']}, remaining={out['credits_remaining']}\n")
    for combo_name, combo in (("SAFE", out["safe_combo"]),
                              ("RISKY", out["risky_combo"]),
                              ("ULTRA-RISKY", out["ultra_combo"])):
        print(f"-- Combo {combo_name} --")
        print(f"  {combo['n_selected']}/{combo['n_total']} sélections · cote {combo['cote']:.2f} · proba {combo['proba']*100:.2f}%")
        for s in combo["selections"]:
            m, p = s["match"], s["pick"]
            print(f"    · {m['home_team']}-{m['away_team']}: {label_for(p['market'], m['home_team'], m['away_team'])} @ {p['unibet_odd']:.2f}")
        if combo["excluded"]:
            print(f"  Exclus du combo (dépasse le cap):")
            for s in combo["excluded"]:
                m, p = s["match"], s["pick"]
                print(f"    × {m['home_team']}-{m['away_team']}: {label_for(p['market'], m['home_team'], m['away_team'])} @ {p['unibet_odd']:.2f}")
        print()
