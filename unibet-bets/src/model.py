"""
Quantitative model: Elo -> Poisson + Dixon-Coles -> market probabilities.

The model converts each team's Elo rating into expected goals (lambda),
builds the bivariate score distribution with Dixon-Coles correction for
the over-representation of low-scoring outcomes, and derives implied
probabilities for every market we want to bet on.

Calibration (well-established in football betting literature):
  - Elo difference / 200 -> expected goal difference (intl football)
  - WC baseline total goals ~ 2.45 (FIFA historical average since 2002)
  - Dixon-Coles rho ~ -0.13
  - 45% of goals occur in the first half historically
  - Home advantage ~ 60 Elo (only applies for host nations: USA/CAN/MEX)
"""

from __future__ import annotations
import json
import math
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# Calibration constants
ELO_TO_GOAL_DIFF_DIVISOR = 200.0
WC_BASELINE_TOTAL_GOALS = 2.45
HOME_ADVANTAGE_ELO = 60.0
DIXON_COLES_RHO = -0.13
FIRST_HALF_GOAL_SHARE = 0.45
MIN_LAMBDA = 0.15
MAX_GOALS = 8  # max goals per team in score matrix

# Hosts of the 2026 World Cup get genuine home advantage when playing at home
HOST_NATIONS = {"USA", "Canada", "Mexico"}


def load_elo() -> dict:
    """Load team Elo ratings from data/team_elo.json."""
    with open(DATA_DIR / "team_elo.json", "r") as f:
        return json.load(f)


def compute_lambdas(
    home_team: str,
    away_team: str,
    elo: dict,
    neutral_venue: bool = True,
    form_data: dict | None = None,
) -> tuple[float, float]:
    """
    Compute expected goals (lambda) for both teams.

    home_team: team listed as home by the data provider
    away_team: team listed as away
    neutral_venue: True for WC group stage (most matches), False only when
                   a host nation is playing at one of its own stadiums
    form_data: optional {team_name: {"form_mult": float, ...}} from
               team_form.load_elo_full(). When provided, each team's λ is
               multiplied by its form multiplier (capped to ±7%) so teams
               near their 12-month peak Elo score slightly more and teams
               in a slump score slightly less, beyond what raw Elo predicts.
    """
    elo_home = elo[home_team]
    elo_away = elo[away_team]

    home_adv = 0.0
    if not neutral_venue and home_team in HOST_NATIONS:
        home_adv = HOME_ADVANTAGE_ELO

    elo_diff = elo_home + home_adv - elo_away
    expected_goal_diff = elo_diff / ELO_TO_GOAL_DIFF_DIVISOR

    lam_home = (WC_BASELINE_TOTAL_GOALS + expected_goal_diff) / 2.0
    lam_away = (WC_BASELINE_TOTAL_GOALS - expected_goal_diff) / 2.0

    if form_data:
        if home_team in form_data:
            lam_home *= form_data[home_team]["form_mult"]
        if away_team in form_data:
            lam_away *= form_data[away_team]["form_mult"]

    return max(lam_home, MIN_LAMBDA), max(lam_away, MIN_LAMBDA)


def poisson_pmf(k: int, lam: float) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def dixon_coles_tau(i: int, j: int, lam_h: float, lam_a: float, rho: float) -> float:
    """
    Dixon-Coles correction for low-scoring matches. Poisson independence
    underestimates 0-0, 1-0, 0-1 and overestimates 1-1; tau fixes that.
    """
    if i == 0 and j == 0:
        return 1.0 - lam_h * lam_a * rho
    if i == 0 and j == 1:
        return 1.0 + lam_h * rho
    if i == 1 and j == 0:
        return 1.0 + lam_a * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(lam_h: float, lam_a: float, rho: float = DIXON_COLES_RHO) -> list[list[float]]:
    """
    Build P[i][j] = probability of final score (home=i, away=j).
    Renormalized to sum to 1.
    """
    matrix = [[0.0] * (MAX_GOALS + 1) for _ in range(MAX_GOALS + 1)]
    total = 0.0
    for i in range(MAX_GOALS + 1):
        ph = poisson_pmf(i, lam_h)
        for j in range(MAX_GOALS + 1):
            pa = poisson_pmf(j, lam_a)
            tau = dixon_coles_tau(i, j, lam_h, lam_a, rho)
            p = ph * pa * tau
            matrix[i][j] = p
            total += p
    if total > 0:
        for i in range(MAX_GOALS + 1):
            for j in range(MAX_GOALS + 1):
                matrix[i][j] /= total
    return matrix


def derive_market_probs(matrix: list[list[float]]) -> dict:
    """
    Derive implied probabilities for every market we care about from the
    full-time score matrix.
    """
    probs = {}

    # 1X2 — home, draw, away
    p_home, p_draw, p_away = 0.0, 0.0, 0.0
    for i in range(len(matrix)):
        for j in range(len(matrix)):
            p = matrix[i][j]
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    probs["h2h_home"] = p_home
    probs["h2h_draw"] = p_draw
    probs["h2h_away"] = p_away

    # Over / Under totals
    for line in [0.5, 1.5, 2.5, 3.5, 4.5]:
        over = 0.0
        for i in range(len(matrix)):
            for j in range(len(matrix)):
                if i + j > line:
                    over += matrix[i][j]
        probs[f"over_{line}"] = over
        probs[f"under_{line}"] = 1.0 - over

    # BTTS
    btts_yes = 0.0
    for i in range(1, len(matrix)):
        for j in range(1, len(matrix)):
            btts_yes += matrix[i][j]
    probs["btts_yes"] = btts_yes
    probs["btts_no"] = 1.0 - btts_yes

    # Asian Handicap (half-goal lines, no push possible)
    for h in [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5]:
        # Home covers handicap h means: home_score + h > away_score
        p_home_covers = 0.0
        for i in range(len(matrix)):
            for j in range(len(matrix)):
                if i + h > j:
                    p_home_covers += matrix[i][j]
        probs[f"ah_home_{h:+.1f}"] = p_home_covers
        probs[f"ah_away_{-h:+.1f}"] = 1.0 - p_home_covers

    return probs


def derive_first_half_probs(lam_h: float, lam_a: float) -> dict:
    """First-half markets derived from a scaled-down Poisson."""
    lam_h_h1 = lam_h * FIRST_HALF_GOAL_SHARE
    lam_a_h1 = lam_a * FIRST_HALF_GOAL_SHARE
    # Dixon-Coles less needed for HT (very low scores already);
    # use rho/2 to soften, low-scoring effect still present.
    matrix = score_matrix(lam_h_h1, lam_a_h1, rho=DIXON_COLES_RHO / 2)
    probs = {}

    p_h, p_d, p_a = 0.0, 0.0, 0.0
    for i in range(len(matrix)):
        for j in range(len(matrix)):
            p = matrix[i][j]
            if i > j:
                p_h += p
            elif i == j:
                p_d += p
            else:
                p_a += p
    probs["h1_h2h_home"] = p_h
    probs["h1_h2h_draw"] = p_d
    probs["h1_h2h_away"] = p_a

    for line in [0.5, 1.5]:
        over = 0.0
        for i in range(len(matrix)):
            for j in range(len(matrix)):
                if i + j > line:
                    over += matrix[i][j]
        probs[f"h1_over_{line}"] = over
        probs[f"h1_under_{line}"] = 1.0 - over

    # BTTS first half
    btts_yes = 0.0
    for i in range(1, len(matrix)):
        for j in range(1, len(matrix)):
            btts_yes += matrix[i][j]
    probs["h1_btts_yes"] = btts_yes
    probs["h1_btts_no"] = 1.0 - btts_yes

    return probs


def predict_match(
    home_team: str,
    away_team: str,
    elo: dict,
    neutral_venue: bool = True,
    form_data: dict | None = None,
) -> dict:
    """
    Full prediction for one match: returns lambdas plus probabilities for
    every market. This is the only function the rest of the pipeline calls.
    """
    lam_h, lam_a = compute_lambdas(
        home_team, away_team, elo,
        neutral_venue=neutral_venue, form_data=form_data,
    )
    matrix = score_matrix(lam_h, lam_a)

    probs = {}
    probs.update(derive_market_probs(matrix))
    probs.update(derive_first_half_probs(lam_h, lam_a))

    return {
        "home_team": home_team,
        "away_team": away_team,
        "lambda_home": round(lam_h, 3),
        "lambda_away": round(lam_a, 3),
        "neutral_venue": neutral_venue,
        "probs": {k: round(v, 4) for k, v in probs.items()},
    }


if __name__ == "__main__":
    # Smoke test on the first match: Mexico vs South Africa (June 11)
    elo = load_elo()
    pred = predict_match("Mexico", "South Africa", elo, neutral_venue=False)
    print(json.dumps(pred, indent=2, ensure_ascii=False))
