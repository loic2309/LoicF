"""
Tournament-form multiplier derived from actual WC 2026 match results.

Once a team has played in the tournament, their real goal differential and
points/game become a stronger signal than pre-tournament Elo alone. This
module computes a per-team multiplicative adjustment in [0.94, 1.06] and
combines it with the existing pre-tournament form_mult from team_form.

Calibration:
  - 1 match played: max ±3% adjustment (small — single match is noisy)
  - 2 matches played: max ±5%
  - 3 matches played: max ±6%
  - 4+ matches (knockout): max ±6% (we stop growing the weight)

Driver: standardized goal-differential per game vs the team's pre-tournament
λ expectation (Elo-based). A team blowing out beyond Elo expectation gets a
boost; a team underperforming gets a penalty.
"""

from __future__ import annotations
from pathlib import Path

from performance import load_results_history
from model import compute_lambdas

DATA_DIR = Path(__file__).parent.parent / "data"


def compute_team_tournament_stats(elo: dict) -> dict:
    """
    Returns {team: {"gp", "gf", "ga", "gd", "pts", "expected_gd_per_game",
                    "delta_per_game", "tournament_form_mult"}}.
    Only teams with ≥ 1 played match get an entry.
    """
    results = load_results_history()

    raw = {}  # team -> aggregate
    for ev_id, r in results.items():
        if not r.get("completed"):
            continue
        hs, as_ = r.get("home_score"), r.get("away_score")
        if hs is None or as_ is None:
            continue
        home, away = r["home_team"], r["away_team"]
        # Pre-tournament expectations for this pairing (Elo only, no form)
        try:
            lam_h_exp, lam_a_exp = compute_lambdas(home, away, elo, neutral_venue=True, form_data=None)
        except KeyError:
            continue
        exp_gd_home = lam_h_exp - lam_a_exp
        actual_gd_home = hs - as_

        for team, exp, actual, pts in [
            (home, exp_gd_home, actual_gd_home, _pts(hs, as_)),
            (away, -exp_gd_home, -actual_gd_home, _pts(as_, hs)),
        ]:
            entry = raw.setdefault(team, {"gp": 0, "gf": 0, "ga": 0,
                                         "pts": 0, "exp_gd_total": 0.0,
                                         "actual_gd_total": 0.0})
            entry["gp"] += 1
            entry["pts"] += pts
            entry["exp_gd_total"] += exp
            entry["actual_gd_total"] += actual
            if team == home:
                entry["gf"] += hs; entry["ga"] += as_
            else:
                entry["gf"] += as_; entry["ga"] += hs

    out = {}
    for team, e in raw.items():
        gp = e["gp"]
        delta_per_game = (e["actual_gd_total"] - e["exp_gd_total"]) / gp
        # Cap growth: weight = min(1, gp/3)
        weight = min(1.0, gp / 3.0)
        # 1 goal of delta per game → ~3% adjustment, scaled by weight
        raw_mult = 1.0 + 0.03 * delta_per_game * weight
        mult = max(0.94, min(1.06, raw_mult))
        out[team] = {
            "gp": gp,
            "gf": e["gf"], "ga": e["ga"],
            "gd": e["gf"] - e["ga"],
            "pts": e["pts"],
            "delta_per_game": round(delta_per_game, 2),
            "tournament_form_mult": round(mult, 4),
        }
    return out


def _pts(a: int, b: int) -> int:
    if a > b: return 3
    if a == b: return 1
    return 0


def combined_form_data(elo: dict, base_form: dict) -> dict:
    """
    Merge pre-tournament form (peak_gap_1y / form_mult) with the new
    tournament-form multiplier. Returns the same shape team_form expects
    plus extra fields for transparency in the UI.
    """
    tourney = compute_team_tournament_stats(elo)
    merged = {}
    for team, base in base_form.items():
        t = tourney.get(team)
        combined_mult = base["form_mult"]
        if t:
            combined_mult = round(base["form_mult"] * t["tournament_form_mult"], 4)
            # Don't push past the same overall ±10% budget
            combined_mult = max(0.90, min(1.10, combined_mult))
        merged[team] = {
            **base,
            "tournament_stats": t,
            "form_mult": combined_mult,
        }
    return merged
