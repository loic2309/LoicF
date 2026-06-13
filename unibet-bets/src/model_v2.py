"""
Model v2 — Elo strength anchor + team-specific attack/defense tilts.

The v1 model derived both teams' expected goals from a single symmetric
Elo difference, so a defensive powerhouse and a spectacle side with the
same Elo produced identical lambdas. v2 fixes that by reading each team's
RECENT REAL GOAL OUTPUT (data/team_profiles.json) and tilting the
Elo-derived lambda up or down for offensive / defensive identity.

Pipeline per match:
  1. lambda_elo_home, lambda_elo_away   (v1 Elo engine — opponent-adjusted)
  2. off_tilt[team]   from recent goals scored vs intl reference
     def_tilt[team]   from recent goals conceded vs intl reference
     (shrunk 60% toward 1.0 because raw recent goals are opponent-
      dependent & minnow-inflated; Elo already carries opponent quality)
  3. lambda_home = lambda_elo_home × off_tilt_home × def_tilt_away × form_live
     lambda_away = lambda_elo_away × off_tilt_away × def_tilt_home × form_live
  4. Dixon-Coles score matrix → ALL market probabilities + top exact scores

Everything is transparent and bounded (tilts clipped to [0.82, 1.20]).
"""

from __future__ import annotations
import json
import math
from pathlib import Path

from model import (
    compute_lambdas, score_matrix, derive_market_probs,
    load_elo, DIXON_COLES_RHO,
)

DATA_DIR = Path(__file__).parent.parent / "data"

# Tilt calibration
SHRINK = 0.40          # fraction of the raw deviation we keep (0.4 = keep 40%)
TILT_FLOOR = 0.82
TILT_CEIL = 1.20

# Name normalization between profiles (no accents) and Elo/odds (accents)
PROFILE_ALIASES = {
    "Curaçao": "Curacao",
    "Ivory Coast": "Ivory Coast",
}


def load_profiles() -> dict:
    with open(DATA_DIR / "team_profiles.json") as f:
        return json.load(f)


def _profile_key(team: str, profiles: dict) -> str | None:
    if team in profiles:
        return team
    alias = PROFILE_ALIASES.get(team)
    if alias and alias in profiles:
        return alias
    # try stripping accents
    import unicodedata
    norm = "".join(c for c in unicodedata.normalize("NFD", team)
                    if unicodedata.category(c) != "Mn")
    for k in profiles:
        if k == norm:
            return k
    return None


def offensive_tilt(gf_per_game: float, ref_gf: float) -> float:
    """>1 if team scores more than a typical WC side, shrunk & clipped."""
    raw = gf_per_game / ref_gf - 1.0
    tilt = 1.0 + SHRINK * raw
    return max(TILT_FLOOR, min(TILT_CEIL, tilt))


def defensive_tilt(ga_per_game: float, ref_ga: float) -> float:
    """>1 if team concedes more than typical (leaky) → opponent scores more."""
    raw = ga_per_game / ref_ga - 1.0
    tilt = 1.0 + SHRINK * raw
    return max(TILT_FLOOR, min(TILT_CEIL, tilt))


def predict_match_v2(home: str, away: str, elo: dict, profiles: dict,
                     neutral_venue: bool = True) -> dict:
    meta = profiles["_meta"]
    ref_gf = meta["intl_ref_gf_per_game"]
    ref_ga = meta["intl_ref_ga_per_game"]

    # 1. Elo anchor (v1)
    lam_h, lam_a = compute_lambdas(home, away, elo, neutral_venue=neutral_venue, form_data=None)

    # 2. Tilts from recent goals
    ph_key = _profile_key(home, profiles)
    pa_key = _profile_key(away, profiles)
    factors = {"home": {}, "away": {}}

    off_h = def_h = off_a = def_a = 1.0
    if ph_key:
        p = profiles[ph_key]
        off_h = offensive_tilt(p["gf_per_game"], ref_gf)
        def_h = defensive_tilt(p["ga_per_game"], ref_ga)
        factors["home"] = {"profile": ph_key, "gf_pg": p["gf_per_game"], "ga_pg": p["ga_per_game"],
                           "off_tilt": round(off_h, 3), "def_tilt": round(def_h, 3),
                           "style": p["style"], "form": p["form"]}
    if pa_key:
        p = profiles[pa_key]
        off_a = offensive_tilt(p["gf_per_game"], ref_gf)
        def_a = defensive_tilt(p["ga_per_game"], ref_ga)
        factors["away"] = {"profile": pa_key, "gf_pg": p["gf_per_game"], "ga_pg": p["ga_per_game"],
                           "off_tilt": round(off_a, 3), "def_tilt": round(def_a, 3),
                           "style": p["style"], "form": p["form"]}

    # 3. Apply: home scoring depends on home attack × away leakiness
    lam_h_v2 = lam_h * off_h * def_a
    lam_a_v2 = lam_a * off_a * def_h
    lam_h_v2 = max(0.12, lam_h_v2)
    lam_a_v2 = max(0.12, lam_a_v2)

    # 4. Score matrix + all probabilities
    matrix = score_matrix(lam_h_v2, lam_a_v2)
    probs = derive_market_probs(matrix)

    # Exact-score top list
    N = len(matrix)
    exact = []
    for i in range(min(N, 6)):
        for j in range(min(N, 6)):
            exact.append(((i, j), matrix[i][j]))
    exact.sort(key=lambda x: -x[1])
    top_scores = [{"score": f"{i}-{j}", "prob": round(p, 4)} for (i, j), p in exact[:6]]

    # Clean sheets
    cs_home = sum(matrix[i][0] for i in range(N))   # away fails to score
    cs_away = sum(matrix[0][j] for j in range(N))   # home fails to score

    return {
        "home_team": home, "away_team": away,
        "lambda_home_elo": round(lam_h, 3), "lambda_away_elo": round(lam_a, 3),
        "lambda_home": round(lam_h_v2, 3), "lambda_away": round(lam_a_v2, 3),
        "expected_total": round(lam_h_v2 + lam_a_v2, 2),
        "factors": factors,
        "clean_sheet_home": round(cs_home, 4),
        "clean_sheet_away": round(cs_away, 4),
        "top_scores": top_scores,
        "probs": {k: round(v, 4) for k, v in probs.items()},
    }


def pretty_report(pred: dict) -> str:
    p = pred["probs"]
    h, a = pred["home_team"], pred["away_team"]
    L = []
    L.append(f"### {h} vs {a}")
    fh = pred["factors"].get("home", {})
    fa = pred["factors"].get("away", {})
    L.append(f"λ Elo: {pred['lambda_home_elo']}–{pred['lambda_away_elo']}  →  "
             f"λ v2 (tilts): **{pred['lambda_home']}–{pred['lambda_away']}**  "
             f"(total attendu {pred['expected_total']})")
    if fh:
        L.append(f"  {h}: {fh['style']}, {fh['gf_pg']} buts/m, {fh['ga_pg']} encaissés/m "
                 f"(off×{fh['off_tilt']}, def×{fh['def_tilt']}) — forme {fh['form']}")
    if fa:
        L.append(f"  {a}: {fa['style']}, {fa['gf_pg']} buts/m, {fa['ga_pg']} encaissés/m "
                 f"(off×{fa['off_tilt']}, def×{fa['def_tilt']}) — forme {fa['form']}")
    L.append(f"1X2: {h} {p['h2h_home']*100:.0f}% · Nul {p['h2h_draw']*100:.0f}% · {a} {p['h2h_away']*100:.0f}%")
    L.append(f"Over/Under: O1.5 {p['over_1.5']*100:.0f}% · O2.5 {p['over_2.5']*100:.0f}% · O3.5 {p['over_3.5']*100:.0f}%")
    L.append(f"BTTS oui {p['btts_yes']*100:.0f}% · Clean sheet {h} {pred['clean_sheet_home']*100:.0f}% · CS {a} {pred['clean_sheet_away']*100:.0f}%")
    L.append("Scores probables: " + " · ".join(f"{s['score']} {s['prob']*100:.0f}%" for s in pred["top_scores"][:5]))
    return "\n".join(L)


if __name__ == "__main__":
    elo = load_elo()
    profiles = load_profiles()
    matches = [
        ("Germany", "Curaçao"),
        ("Netherlands", "Japan"),
        ("Ivory Coast", "Ecuador"),
        ("Sweden", "Tunisia"),
    ]
    for h, a in matches:
        pred = predict_match_v2(h, a, elo, profiles, neutral_venue=True)
        print(pretty_report(pred))
        print()
