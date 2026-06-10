"""
Auto-generated qualitative narrative per match.

Inputs: prediction (lambdas + market probs), consensus, form data, Unibet
odds, the chosen safe/risky picks. Output: a 1-line French narrative
summarizing the betting angle for the match — picks up:
  - Favorite intensity (one-sided vs balanced)
  - Goal expectations (low/medium/high scoring)
  - Form mismatches (in-form outsider vs slumping favorite, etc.)
  - Model-vs-market divergence ("trap match")
  - "Surprise potential" when an underdog has a >25% real shot
"""

from __future__ import annotations


def _strongest_h2h(probs: dict) -> tuple[str, float]:
    h, d, a = probs.get("h2h_home", 0), probs.get("h2h_draw", 0), probs.get("h2h_away", 0)
    if h >= d and h >= a:
        return "home", h
    if a >= h and a >= d:
        return "away", a
    return "draw", d


def generate(match: dict, form_data: dict | None = None) -> str:
    pred = match["prediction"]
    probs = pred["probs"]
    home, away = match["home_team"], match["away_team"]
    lam_h, lam_a = pred["lambda_home"], pred["lambda_away"]
    total_lam = lam_h + lam_a

    fragments = []

    # Favorite intensity
    fav, fav_prob = _strongest_h2h(probs)
    fav_name = home if fav == "home" else (away if fav == "away" else None)

    if fav_prob >= 0.65:
        fragments.append(f"<b>{fav_name}</b> ultra-favori (modèle&nbsp;{fav_prob*100:.0f}%)")
    elif fav_prob >= 0.50:
        fragments.append(f"<b>{fav_name}</b> favori (modèle&nbsp;{fav_prob*100:.0f}%)")
    elif fav == "draw":
        fragments.append(f"match&nbsp;<b>très équilibré</b> (nul modèle&nbsp;{fav_prob*100:.0f}%)")
    else:
        fragments.append(f"match&nbsp;<b>ouvert</b>, <b>{fav_name}</b> légèrement favori ({fav_prob*100:.0f}%)")

    # Goal expectations
    if total_lam <= 1.9:
        fragments.append(f"attendu défensif ({lam_h:.1f} – {lam_a:.1f}, Under conseillé)")
    elif total_lam >= 3.2:
        fragments.append(f"match à <b>fort attendu de buts</b> ({lam_h:.1f} + {lam_a:.1f})")

    # Form signals
    if form_data:
        f_home = form_data.get(home, {})
        f_away = form_data.get(away, {})
        fc_home = f_home.get("form_class", "stable")
        fc_away = f_away.get("form_class", "stable")
        if fc_home == "in_form" and fc_away == "slump":
            fragments.append(f"<b>{home}</b> proche de son pic Elo face à <b>{away}</b> en méforme")
        elif fc_away == "in_form" and fc_home == "slump":
            fragments.append(f"<b>{away}</b> en forme contre <b>{home}</b> en méforme — <b>piège possible</b>")
        elif fc_home == "in_form" and fav == "home":
            fragments.append(f"{home} confirme son momentum")
        elif fc_away == "in_form" and fav == "away":
            fragments.append(f"{away} confirme son momentum")
        elif fc_home == "slump" and fav == "home":
            fragments.append(f"⚠️ {home} favori mais en méforme, prudence")
        elif fc_away == "slump" and fav == "away":
            fragments.append(f"⚠️ {away} favori mais en méforme, prudence")

    # Model vs market divergence (trap detection)
    cons = match.get("consensus_probs", {})
    if cons:
        model_h = probs.get("h2h_home", 0)
        market_h = cons.get("h2h_home", 0)
        diff = model_h - market_h
        if abs(diff) >= 0.10:
            if diff > 0:
                fragments.append(
                    f"modèle plus optimiste que le marché sur {home} ({model_h*100:.0f}% vs {market_h*100:.0f}%)"
                )
            else:
                fragments.append(
                    f"modèle plus pessimiste que le marché sur {home} ({model_h*100:.0f}% vs {market_h*100:.0f}%)"
                )

    # Surprise / upset potential
    upset_prob = probs.get("h2h_away", 0) if fav == "home" else probs.get("h2h_home", 0) if fav == "away" else 0
    if 0.25 <= upset_prob <= 0.42:
        underdog = away if fav == "home" else home
        fragments.append(f"<b>{underdog}</b> garde {upset_prob*100:.0f}% de surprendre")

    return " · ".join(fragments) + "."
