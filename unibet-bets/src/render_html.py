"""
Render bets.html — today's Belgian window only, 3-tier picks + combos.

The page is intentionally narrow: it shows the matches kicking off in the
current Belgian "day" window (15:00 → next 06:00) and three combos with
their respective caps. A refresh button calls /refresh on the local
serve.py, which re-runs the pipeline and reloads the page.
"""

from __future__ import annotations
import html
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bet_selector import (
    analyse_today, label_for,
    SAFE_COMBO_CAP, RISKY_COMBO_CAP,
    SAFE_STAKE, RISKY_STAKE, ULTRA_STAKE,
    belgian_day_window,
)
from performance import (
    evaluate_all, evaluate_combos, aggregate_combos,
    tournament_standings, CATEGORY_LABELS, STAKES,
)

OUT = Path(__file__).parent.parent / "bets.html"
BE_TZ = ZoneInfo("Europe/Brussels")

FR_DAYS = {"Monday":"Lundi","Tuesday":"Mardi","Wednesday":"Mercredi","Thursday":"Jeudi",
           "Friday":"Vendredi","Saturday":"Samedi","Sunday":"Dimanche"}
FR_MONTHS = {"January":"janvier","February":"février","March":"mars","April":"avril",
             "May":"mai","June":"juin","July":"juillet","August":"août","September":"septembre",
             "October":"octobre","November":"novembre","December":"décembre"}

FORM_BADGE = {
    "in_form": ("🔥", "in-form", "En forme — proche du pic Elo des 12 derniers mois"),
    "stable":  ("●",  "stable",  "Forme stable, à distance modérée du pic récent"),
    "slump":   ("❄️", "slump",   "Méforme — Elo nettement sous le pic des 12 derniers mois"),
}


def fmt_kickoff_be(iso_utc: str) -> str:
    return datetime.fromisoformat(iso_utc.replace("Z","+00:00")).astimezone(BE_TZ).strftime("%H:%M")


def fmt_window_label(start_iso: str, end_iso: str) -> str:
    s = datetime.fromisoformat(start_iso).astimezone(BE_TZ)
    e = datetime.fromisoformat(end_iso).astimezone(BE_TZ)
    day_label = f"{FR_DAYS[s.strftime('%A')]} {s.day} {FR_MONTHS[s.strftime('%B')]} {s.year}"
    return f"{day_label} · matchs entre {s.strftime('%H:%M')} et {e.strftime('%H:%M')} (heure belge)"


def form_chip(form: dict | None) -> str:
    if not form:
        return ''
    cls = form.get("form_class", "stable")
    icon, css, tooltip = FORM_BADGE.get(cls, FORM_BADGE["stable"])
    gap = form.get("peak_gap_1y", 0)
    return f'<span class="form-chip form-{css}" title="{tooltip} ({gap:+d} Elo)">{icon}</span>'


def pick_card(pick: dict | None, kind: str, home: str, away: str, label_text: str) -> str:
    if pick is None:
        return f"""
      <div class="pick {kind} empty">
        <div class="pick-label">{label_text}</div>
        <div class="pick-empty">Pas de pari sélectionnable dans cette catégorie</div>
      </div>"""
    edge_pct = pick["edge"] * 100
    fair_pct = pick["fair_prob"] * 100
    model_pct = (pick["model_prob"] or 0) * 100
    cons_pct = (pick["consensus_prob"] or 0) * 100
    label = label_for(pick["market"], home, away)
    odd = pick["unibet_odd"]
    has_value = pick.get("has_value", False)
    badge = '<span class="value-tag yes">★ VALEUR</span>' if has_value else '<span class="value-tag no">pas de valeur</span>'
    edge_sign = "+" if edge_pct >= 0 else ""

    detail_html = f'<span>Modèle: {model_pct:.1f}%</span>'
    if pick.get("consensus_prob") is not None:
        detail_html += f'<span>Marché: {cons_pct:.1f}%</span>'

    note_html = ""
    if pick.get("note"):
        note_html = f'<div class="pick-note">⚠️ {html.escape(pick["note"])}</div>'

    return f"""
      <div class="pick {kind} {'has-value' if has_value else 'no-value'}">
        <div class="pick-label">{label_text} {badge}</div>
        <div class="pick-name">{html.escape(label)}</div>
        <div class="pick-odd">@ {odd:.2f}</div>
        <div class="pick-stats">
          <span>Proba équitable <b>{fair_pct:.1f}%</b></span>
          <span class="edge">Edge {edge_sign}{edge_pct:.1f}%</span>
        </div>
        <div class="pick-detail">{detail_html}</div>
        {note_html}
      </div>"""


def match_card(match: dict) -> str:
    kickoff = fmt_kickoff_be(match["commence_time"])
    pred = match["prediction"]
    home, away = match["home_team"], match["away_team"]
    f_home, f_away = match.get("form_home"), match.get("form_away")
    players_h = match.get("key_players_home", [])
    players_a = match.get("key_players_away", [])

    if not match.get("unibet_odds"):
        return f"""
      <div class="match no-odds">
        <div class="match-header">
          <span class="kickoff">{kickoff}</span>
          <span class="teams"><b>{html.escape(home)}</b>{form_chip(f_home)} <em>vs</em> <b>{html.escape(away)}</b>{form_chip(f_away)}</span>
        </div>
        <div class="no-odds-msg">Cotes Unibet non disponibles pour ce match</div>
      </div>"""

    odds = match["unibet_odds"]
    h2h_line = ""
    if all(k in odds for k in ("h2h_home","h2h_draw","h2h_away")):
        h2h_line = (
            f'<span class="odd-block"><span class="o-label">1</span> <b>{odds["h2h_home"]:.2f}</b></span>'
            f'<span class="odd-block"><span class="o-label">N</span> <b>{odds["h2h_draw"]:.2f}</b></span>'
            f'<span class="odd-block"><span class="o-label">2</span> <b>{odds["h2h_away"]:.2f}</b></span>'
        )

    insight_html = ''
    if match.get("insight"):
        insight_html = f'<div class="match-insight">💡 {match["insight"]}</div>'

    players_html = ''
    if players_h or players_a:
        bits = []
        if players_h:
            bits.append(f'<span class="player-side"><b>{html.escape(home)}</b> : {html.escape(", ".join(players_h))}</span>')
        if players_a:
            bits.append(f'<span class="player-side"><b>{html.escape(away)}</b> : {html.escape(", ".join(players_a))}</span>')
        players_html = f'<div class="match-players">⭐ {" · ".join(bits)}</div>'

    return f"""
      <div class="match">
        <div class="match-header">
          <span class="kickoff">{kickoff}</span>
          <span class="teams"><b>{html.escape(home)}</b>{form_chip(f_home)} <em>vs</em> <b>{html.escape(away)}</b>{form_chip(f_away)}</span>
          <span class="lambdas" title="Buts attendus (forme incluse)">⚽ {pred['lambda_home']:.2f} – {pred['lambda_away']:.2f}</span>
        </div>
        {insight_html}
        {players_html}
        <div class="match-odds">{h2h_line}</div>
        <div class="picks three">
          {pick_card(match['safe'], 'safe', home, away, '🛡️ SAFE')}
          {pick_card(match['risky'], 'risky', home, away, '⚡ RISQUÉ')}
          {pick_card(match['ultra_risky'], 'ultra', home, away, '🎲 ULTRA-RISQUÉ')}
        </div>
      </div>"""


def combo_panel(combo: dict, title: str, stake: float, cap: float | None, css_kind: str) -> str:
    icon = {"safe":"🛡️","risky":"⚡","ultra":"🎲"}[css_kind]
    sels = combo["selections"]
    excluded = combo.get("excluded", [])

    if not sels:
        return f"""
        <div class="combo {css_kind} empty">
          <h3>{icon} {title}</h3>
          <div class="empty-msg">Aucun pari sélectionnable aujourd'hui dans cette catégorie.</div>
        </div>"""

    rows = []
    for s in sels:
        m, p = s["match"], s["pick"]
        kickoff = fmt_kickoff_be(m["commence_time"])
        value_chip = '<span class="combo-tag tag-value">★ VALEUR</span>' if p.get("has_value") else ''
        is_scorer = p["market"].startswith("scorer_")
        market_icon = "⚽" if is_scorer else ""
        rows.append(
            f'<li>'
            f'<span class="combo-match"><span class="combo-time">{kickoff}</span>'
            f'{html.escape(m["home_team"])} – {html.escape(m["away_team"])}</span>'
            f'<span class="combo-pick">{value_chip}'
            f'<b>{market_icon} {html.escape(label_for(p["market"], m["home_team"], m["away_team"]))}</b> '
            f'@ {p["unibet_odd"]:.2f}</span></li>'
        )

    excl_html = ''
    if excluded:
        excl_rows = []
        for s in excluded:
            m, p = s["match"], s["pick"]
            kickoff = fmt_kickoff_be(m["commence_time"])
            excl_rows.append(
                f'<li><span class="combo-time">{kickoff}</span>'
                f'{html.escape(m["home_team"])} – {html.escape(m["away_team"])} : '
                f'{html.escape(label_for(p["market"], m["home_team"], m["away_team"]))} '
                f'@ {p["unibet_odd"]:.2f}</li>'
            )
        excl_html = f'<div class="combo-excluded">Exclus du combo pour respecter le cap : <ul>{"".join(excl_rows)}</ul></div>'

    payout = combo["cote"] * stake
    ev_pct = (combo["proba"] * combo["cote"] - 1) * 100
    cap_str = f"Cap {cap:.0f}" if cap is not None else "Sans plafond"

    return f"""
        <div class="combo {css_kind}">
          <h3>{icon} {title} <span class="combo-meta">{cap_str} · Mise {stake:.0f}€</span></h3>
          <ul>{''.join(rows)}</ul>
          {excl_html}
          <div class="total">
            <span class="total-main">Cote combinée <span class="cote">{combo['cote']:.2f}</span></span>
            <span>Proba {combo['proba']*100:.2f}%</span>
            <span>EV {ev_pct:+.1f}%</span>
            <span class="payout">Mise {stake:.0f}€ → gain {payout:.2f}€</span>
          </div>
        </div>"""


OUTCOME_STYLE = {
    "won":            ("✓",  "outcome-won",    "Pari gagné"),
    "lost":           ("✗",  "outcome-lost",   "Pari perdu"),
    "pending":        ("⏳", "outcome-pending","En attente du résultat"),
    "manual_pending": ("?",  "outcome-manual", "Pari buteur — à marquer manuellement"),
    "refund":         ("≈",  "outcome-refund", "Pari remboursé (joueur n'a pas joué, etc.)"),
}


def render_performance_tab(perf: dict, combos: list, combo_totals: dict, standings: dict) -> str:
    """
    Combo-based reporting. The user actually plays the combo of all picks
    in a category — a combo wins iff every leg wins. Individual leg outcomes
    are still kept in the history table for transparency, but ROI/P&L are
    computed on combos.
    """
    rows = perf["rows"]

    # Summary cards per category — combo-based
    summary_cards = []
    for category in ("safe", "risky", "ultra_risky"):
        t = combo_totals.get(category, {"n_combos":0,"wins":0,"losses":0,
                                       "pending":0,"profit":0,"roi":0,
                                       "hit_rate":0,"stake_total":0})
        css = {"safe":"safe","risky":"risky","ultra_risky":"ultra"}[category]
        profit_sign = "+" if t["profit"] >= 0 else ""
        roi_sign = "+" if t["roi"] >= 0 else ""
        summary_cards.append(f"""
        <div class="perf-card {css}">
          <div class="perf-card-head">{CATEGORY_LABELS[category]} · mise {STAKES[category]:.0f}€/combo</div>
          <div class="perf-card-pl">
            <span class="pl-amount {'gain' if t['profit'] >= 0 else 'loss'}">{profit_sign}{t['profit']:.2f}€</span>
            <span class="pl-roi {'gain' if t['roi'] >= 0 else 'loss'}">ROI {roi_sign}{t['roi']*100:.1f}%</span>
          </div>
          <div class="perf-card-stats">
            <div><span class="stat-num">{t['wins']}</span><span class="stat-lbl">combos gagnés</span></div>
            <div><span class="stat-num">{t['losses']}</span><span class="stat-lbl">combos perdus</span></div>
            <div><span class="stat-num">{t['pending']}</span><span class="stat-lbl">en attente</span></div>
            <div><span class="stat-num">{t['hit_rate']*100:.0f}%</span><span class="stat-lbl">hit-rate</span></div>
          </div>
        </div>""")

    # Standings table
    standings_html = ""
    if standings:
        std_rows = []
        for team, row in list(standings.items())[:16]:
            std_rows.append(
                f'<tr><td>{html.escape(team)}</td>'
                f'<td>{row["gp"]}</td><td>{row["w"]}</td><td>{row["d"]}</td><td>{row["l"]}</td>'
                f'<td>{row["gf"]}-{row["ga"]}</td><td class="gd">{row["gd"]:+d}</td>'
                f'<td class="pts">{row["pts"]}</td></tr>'
            )
        standings_html = f"""
        <div class="standings">
          <h3>📊 Classement du tournoi (équipes ayant joué)</h3>
          <table class="standings-table">
            <thead><tr><th>Équipe</th><th>J</th><th>V</th><th>N</th><th>D</th><th>Buts</th><th>+/−</th><th>Pts</th></tr></thead>
            <tbody>{''.join(std_rows)}</tbody>
          </table>
        </div>"""
    else:
        standings_html = ('<div class="standings empty">📊 Classement à venir — '
                          'aucun match terminé pour le moment.</div>')

    # Picks history table
    if rows:
        hist_rows = []
        for r in rows:
            icon, css, tooltip = OUTCOME_STYLE.get(r["outcome"], ("?","outcome-pending","?"))
            kickoff_be = fmt_kickoff_be(r["kickoff"])
            kickoff_day = datetime.fromisoformat(
                r["kickoff"].replace("Z","+00:00")
            ).astimezone(BE_TZ).strftime("%d/%m")
            cat_icon = {"safe":"🛡️","risky":"⚡","ultra_risky":"🎲"}[r["category"]]
            pick_lbl = label_for(r["market"], r["home"], r["away"])
            result_cell = r["result_text"] or "—"
            profit_cell = ""
            if r["outcome"] in ("won","lost"):
                sign = "+" if r["profit"] >= 0 else ""
                profit_cell = f'<span class="{"gain" if r["profit"]>=0 else "loss"}">{sign}{r["profit"]:.2f}€</span>'

            # Manual buttons for buteur picks
            manual_buttons = ""
            if r["outcome"] == "manual_pending":
                manual_buttons = (
                    f'<button class="mini-btn mini-win" '
                    f'onclick="markOutcome(\'{r["event_id"]}\',\'{r["category"]}\',\'won\')">✓</button>'
                    f'<button class="mini-btn mini-lose" '
                    f'onclick="markOutcome(\'{r["event_id"]}\',\'{r["category"]}\',\'lost\')">✗</button>'
                )

            hist_rows.append(f"""
              <tr class="row-{r['outcome']}">
                <td class="date">{kickoff_day} {kickoff_be}</td>
                <td class="match">{html.escape(r['home'])} – {html.escape(r['away'])}</td>
                <td class="cat">{cat_icon}</td>
                <td class="pick">{html.escape(pick_lbl)}</td>
                <td class="cote">@ {r['cote']:.2f}</td>
                <td class="stake">{r['stake']:.0f}€</td>
                <td class="result">{result_cell}</td>
                <td class="status {css}" title="{tooltip}">{icon} {manual_buttons}</td>
                <td class="pl">{profit_cell}</td>
              </tr>""")
        history_html = f"""
        <div class="history">
          <h3>📋 Historique des paris ({len(rows)})</h3>
          <table class="history-table">
            <thead><tr>
              <th>Date</th><th>Match</th><th>Cat</th><th>Pari</th>
              <th>Cote</th><th>Mise</th><th>Score</th><th>Statut</th><th>P/L</th>
            </tr></thead>
            <tbody>{''.join(hist_rows)}</tbody>
          </table>
        </div>"""
    else:
        history_html = ('<div class="history empty">📋 Aucun pari enregistré pour le moment. '
                        'Reviens après le premier match.</div>')

    # Grand total — combo-based
    total_n = sum(t.get("n_combos",0) for t in combo_totals.values())
    total_profit = sum(t.get("profit",0) for t in combo_totals.values())
    total_stake = sum(t.get("stake_total",0) for t in combo_totals.values())
    total_roi = (total_profit / total_stake) if total_stake > 0 else 0
    sign = "+" if total_profit >= 0 else ""
    grand_total_html = f"""
      <div class="grand-total">
        <div><span class="gt-num {'gain' if total_profit>=0 else 'loss'}">{sign}{total_profit:.2f}€</span><span class="gt-lbl">profit total (combos)</span></div>
        <div><span class="gt-num {'gain' if total_roi>=0 else 'loss'}">{sign}{total_roi*100:.1f}%</span><span class="gt-lbl">ROI cumulé</span></div>
        <div><span class="gt-num">{total_n}</span><span class="gt-lbl">combos joués</span></div>
        <div><span class="gt-num">{total_stake:.0f}€</span><span class="gt-lbl">mise totale</span></div>
      </div>"""

    # Combo history — one row per (Belgian day × category)
    combo_html = ""
    if combos:
        crows = []
        for c in combos:
            cat_icon = {"safe":"🛡️","risky":"⚡","ultra_risky":"🎲"}[c["category"]]
            cat_label = {"safe":"Safe","risky":"Risqué","ultra_risky":"Ultra"}[c["category"]]
            day_label = c["belgian_day"][8:10] + "/" + c["belgian_day"][5:7]
            icon, css, _ = OUTCOME_STYLE.get(c["outcome"], ("?","outcome-pending","?"))
            sign = "+" if c["profit"] >= 0 else ""
            profit_cell = f'<span class="{"gain" if c["profit"]>=0 else "loss"}">{sign}{c["profit"]:.2f}€</span>' if c["outcome"] in ("won","lost") else ""
            legs_txt = " × ".join(
                f'{label_for(p["market"], p["home"], p["away"])} @ {p["cote"]:.2f}'
                for p in c["legs"]
            )
            crows.append(f"""
              <tr class="row-{c['outcome']}">
                <td class="date">{day_label}</td>
                <td class="cat">{cat_icon} {cat_label}</td>
                <td class="legs">{html.escape(legs_txt)}</td>
                <td class="cote">{c['cote_combined']:.2f}</td>
                <td class="stake">{c['stake']:.0f}€</td>
                <td class="status {css}">{icon}</td>
                <td class="pl">{profit_cell}</td>
              </tr>""")
        combo_html = f"""
        <div class="history">
          <h3>🎯 Combinés joués ({len(combos)})</h3>
          <table class="history-table combo-history-table">
            <thead><tr>
              <th>Date</th><th>Cat</th><th>Sélections</th>
              <th>Cote</th><th>Mise</th><th>Statut</th><th>P/L</th>
            </tr></thead>
            <tbody>{''.join(crows)}</tbody>
          </table>
        </div>"""
    else:
        combo_html = '<div class="history empty">🎯 Aucun combiné joué pour le moment.</div>'

    return f"""
    <div class="perf-tools">
      <button class="refresh-btn small" onclick="updateResults()">🔄 Mettre à jour les résultats</button>
      <span id="update-status" class="refresh-status"></span>
      <span class="perf-note">ROI calculé sur les combinés. Un combo perd si UNE seule jambe perd.</span>
    </div>
    {grand_total_html}
    <div class="perf-summary">{''.join(summary_cards)}</div>
    {combo_html}
    {standings_html}
    <details class="leg-details"><summary>Détail des paris individuels (transparence)</summary>
      {history_html}
    </details>"""


def render(analysis: dict) -> str:
    matches = analysis["matches"]
    window_label = fmt_window_label(analysis["window_start"], analysis["window_end"])

    perf = evaluate_all()
    combos = evaluate_combos(perf["rows"])
    combo_totals = aggregate_combos(combos)
    standings = tournament_standings()

    rolled_banner = ""
    if analysis.get("auto_rolled"):
        rolled_banner = '<div class="rolled-banner">ℹ️ Aucun match Coupe du Monde dans la fenêtre du jour. Affichage de la prochaine fenêtre disponible.</div>'

    if matches:
        match_cards = "\n".join(match_card(m) for m in matches)
        body = f"""
    {rolled_banner}
    <section class="day">
      <h2>⚽ {window_label} <span class="day-count">{len(matches)} match{'s' if len(matches) > 1 else ''}</span></h2>
      <div class="matches">{match_cards}</div>
      <div class="combos">
        {combo_panel(analysis['safe_combo'], 'Combiné SAFE', SAFE_STAKE, SAFE_COMBO_CAP, 'safe')}
        {combo_panel(analysis['risky_combo'], 'Combiné RISQUÉ', RISKY_STAKE, RISKY_COMBO_CAP, 'risky')}
        {combo_panel(analysis['ultra_combo'], 'Combiné ULTRA-RISQUÉ', ULTRA_STAKE, None, 'ultra')}
      </div>
    </section>"""
    else:
        body = f"""
    <section class="day empty-day">
      <h2>⚽ {window_label}</h2>
      <div class="empty-day-msg">
        Aucun match Coupe du Monde dans cette fenêtre.<br>
        Clique sur <b>Rafraîchir</b> demain ou consulte la fenêtre suivante après 15h.
      </div>
    </section>"""

    fetched_local = datetime.fromisoformat(
        analysis["fetched_at"].replace("Z","+00:00")
    ).astimezone(BE_TZ).strftime("%d/%m/%Y %H:%M")
    cu, cr = analysis["credits_used"], analysis["credits_remaining"]

    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Paris du jour · Coupe du Monde 2026 · Unibet</title>
  <style>
    :root {{
      --pitch: #0a7e3f; --pitch-dark: #065c2c; --pitch-light: #e8f5ec;
      --field-stripe: #f4faf6; --white: #ffffff; --ink: #0e1a13;
      --ink-soft: #4a5a52; --ink-dim: #8a9991; --line: #e3e9e5;
      --safe: #0a7e3f; --safe-soft: #e3f3e8; --safe-strong: #065c2c;
      --risky: #c4441a; --risky-soft: #fdeee6; --risky-strong: #8e2f10;
      --ultra: #6e34c4; --ultra-soft: #f1e9fa; --ultra-strong: #4a1c87;
      --yellow-card: #f5c518; --accent: #1976d2;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin:0; padding:0; }}
    body {{
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: linear-gradient(180deg, var(--field-stripe) 0%, var(--white) 200px), var(--white);
      color: var(--ink); line-height: 1.45; -webkit-font-smoothing: antialiased;
    }}
    header {{
      background: radial-gradient(ellipse at 20% 0%, rgba(10,126,63,.18), transparent 50%),
                  linear-gradient(135deg, var(--pitch) 0%, var(--pitch-dark) 100%);
      color: var(--white); padding: 24px 36px 20px; position: relative; overflow: hidden;
    }}
    header::before {{ content:""; position:absolute; right:-40px; top:-40px; width:180px; height:180px; border:3px solid rgba(255,255,255,.08); border-radius:50%; }}
    header::after  {{ content:""; position:absolute; right:40px; bottom:-80px; width:240px; height:240px; border:3px solid rgba(255,255,255,.06); border-radius:50%; }}
    header h1 {{ margin:0; font-size:24px; font-weight:800; letter-spacing:-0.3px; display:flex; align-items:center; gap:12px; }}
    header h1 .badge {{ background:var(--yellow-card); color:var(--ink); padding:2px 10px; border-radius:4px; font-size:11px; font-weight:800; letter-spacing:1px; }}
    header .sub {{ margin-top:8px; font-size:12.5px; opacity:.88; display:flex; gap:14px; flex-wrap:wrap; }}
    header .pill {{ display:inline-block; background:rgba(255,255,255,.13); padding:2px 10px; border-radius:12px; font-weight:500; }}
    .header-actions {{ position:absolute; top:24px; right:36px; display:flex; gap:10px; align-items:center; }}
    .refresh-btn {{
      background: var(--yellow-card); color: var(--ink); border:0; padding: 10px 16px; border-radius:6px;
      font-weight:700; cursor:pointer; font-size:13px; box-shadow: 0 2px 8px rgba(0,0,0,.18);
      transition: transform .12s, opacity .12s;
    }}
    .refresh-btn:hover {{ transform: translateY(-1px); }}
    .refresh-btn:disabled {{ opacity:.6; cursor: wait; transform: none; }}
    .refresh-status {{ font-size:11.5px; color: var(--white); opacity:.85; }}
    .legend {{ padding: 12px 36px; background: var(--pitch-light); border-bottom:1px solid var(--line); display:flex; gap:18px; font-size:12px; color: var(--ink-soft); flex-wrap:wrap; }}
    .legend b {{ color: var(--ink); font-weight:600; }}
    .transparency {{
      padding: 11px 36px; background: #fffaee; border-bottom: 1px solid var(--line);
      font-size: 11.5px; color: var(--ink-soft); display:flex; gap:16px; flex-wrap:wrap;
    }}
    .transparency strong {{ color: var(--ink); }}
    .transparency b {{ color: var(--ink); font-weight:600; }}
    details.explainer {{ margin: 12px 36px 0; padding:0; background: var(--white); border:1px solid var(--line); border-radius:6px; transition: box-shadow .15s; }}
    details.explainer[open] {{ box-shadow: 0 2px 8px rgba(14,26,19,.06); border-color: var(--pitch); }}
    details.explainer summary {{ padding: 12px 18px; cursor:pointer; font-size:13px; color: var(--ink-soft); list-style:none; user-select:none; display:flex; align-items:center; gap:8px; }}
    details.explainer summary::-webkit-details-marker {{ display:none; }}
    details.explainer summary::after {{ content:"▾"; margin-left:auto; color: var(--ink-dim); transition: transform .15s; }}
    details.explainer[open] summary::after {{ transform: rotate(180deg); }}
    details.explainer summary b {{ color: var(--ink); font-weight:600; }}
    details.explainer summary:hover {{ background: var(--pitch-light); }}
    .explainer-body {{ padding: 4px 22px 18px; font-size: 13px; color: var(--ink-soft); line-height:1.55; border-top:1px solid var(--line); }}
    .explainer-body p {{ margin: 10px 0; }}
    .explainer-body b {{ color: var(--ink); font-weight:600; }}
    .explainer-body h4 {{ margin: 18px 0 6px; font-size: 14px; font-weight:700; color: var(--ink); padding-bottom:4px; border-bottom:1px solid var(--line); }}
    .explainer-body .formula {{ background: var(--pitch-light); border-left:3px solid var(--pitch); padding:8px 14px; font-family:"SF Mono",Menlo,Consolas,monospace; font-size:12.5px; color:var(--ink); margin:8px 0; border-radius:0 4px 4px 0; }}
    .explainer-body .warn {{ background: #fff7e0; border-left:3px solid var(--yellow-card); padding:9px 14px; border-radius:0 4px 4px 0; color: var(--ink); }}
    main {{ padding: 14px 36px 56px; max-width: 1240px; margin: 0 auto; }}
    .day {{ margin-top: 14px; }}
    .day h2 {{ font-size: 17px; font-weight:700; margin:0 0 14px; padding:10px 0 10px 4px; border-bottom:2px solid var(--pitch); color: var(--ink); display:flex; align-items:center; gap:10px; }}
    .day-count {{ color: var(--ink-dim); font-weight:500; font-size:12px; margin-left:auto; background: var(--pitch-light); padding:2px 10px; border-radius:10px; }}
    .empty-day-msg {{ padding: 40px 20px; text-align:center; font-size: 14px; color: var(--ink-soft); background: var(--pitch-light); border-radius:8px; border:1px solid var(--line); }}
    .rolled-banner {{ padding:10px 16px; background:#fff7e0; border:1px solid rgba(245,197,24,.5); border-radius:6px; font-size:12.5px; color:var(--ink); margin: 10px 0 4px; }}
    .matches {{ display: grid; gap: 12px; }}
    .match {{ background:var(--white); border:1px solid var(--line); border-left:4px solid var(--pitch); border-radius:6px; padding:14px 18px; box-shadow: 0 1px 2px rgba(14,26,19,.04); transition: transform .12s, box-shadow .12s; }}
    .match:hover {{ box-shadow: 0 4px 14px rgba(14,26,19,.08); transform: translateY(-1px); }}
    .match.no-odds {{ opacity:.55; border-left-color:var(--ink-dim); }}
    .match-header {{ display:flex; align-items:center; gap:16px; margin-bottom:10px; }}
    .kickoff {{ font-variant-numeric: tabular-nums; color:var(--white); background:var(--pitch); font-size:13px; font-weight:700; padding:4px 10px; border-radius:4px; min-width:56px; text-align:center; }}
    .teams {{ font-weight:500; font-size:15.5px; flex:1; }}
    .teams b {{ font-weight:700; }}
    .teams em {{ color:var(--ink-dim); font-style:normal; margin:0 8px; font-size:12px; font-weight:400; }}
    .form-chip {{ display:inline-block; font-size:11px; padding:1px 5px; border-radius:9px; margin-left:5px; vertical-align:middle; cursor:help; }}
    .form-chip.form-in-form {{ background:#fff4d9; }}
    .form-chip.form-stable  {{ background:#eef2ef; color:var(--ink-dim); }}
    .form-chip.form-slump   {{ background:#e6f1fb; }}
    .lambdas {{ color:var(--ink-soft); font-size:12px; font-variant-numeric:tabular-nums; background:var(--pitch-light); padding:3px 9px; border-radius:10px; }}
    .match-insight {{ font-size:12.5px; color:var(--ink-soft); margin:0 0 10px; padding:7px 11px; background:var(--pitch-light); border-left:3px solid var(--pitch); border-radius:0 4px 4px 0; line-height:1.45; }}
    .match-insight b {{ color:var(--ink); font-weight:600; }}
    .match-players {{ font-size:11.5px; color:var(--ink-soft); margin:0 0 12px; padding:0 4px; display:flex; gap:12px; flex-wrap:wrap; }}
    .match-odds {{ display:flex; gap:10px; font-size:12.5px; margin-bottom:12px; padding-bottom:10px; border-bottom:1px dashed var(--line); }}
    .odd-block {{ background:#f6f8f7; border:1px solid var(--line); border-radius:4px; padding:3px 10px; display:inline-flex; gap:6px; align-items:baseline; }}
    .o-label {{ color:var(--ink-dim); font-weight:700; font-size:11px; }}
    .odd-block b {{ color:var(--ink); font-variant-numeric:tabular-nums; font-weight:700; }}
    .picks.three {{ display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; }}
    .pick {{ padding:11px 13px; border-radius:6px; border:1px solid var(--line); background:var(--white); position:relative; }}
    .pick.safe {{ background:var(--safe-soft); border-color:rgba(10,126,63,.32); }}
    .pick.risky {{ background:var(--risky-soft); border-color:rgba(196,68,26,.32); }}
    .pick.ultra {{ background:var(--ultra-soft); border-color:rgba(110,52,196,.32); }}
    .pick.empty {{ background:#f6f8f7; border-style:dashed; }}
    .pick.no-value {{ filter: grayscale(.55); opacity:.82; }}
    .pick-label {{ font-size:10.5px; font-weight:800; letter-spacing:1.2px; margin-bottom:5px; display:flex; align-items:center; gap:4px; flex-wrap:wrap; }}
    .safe .pick-label {{ color:var(--safe-strong); }}
    .risky .pick-label {{ color:var(--risky-strong); }}
    .ultra .pick-label {{ color:var(--ultra-strong); }}
    .pick.empty .pick-label {{ color:var(--ink-dim); }}
    .pick-empty {{ font-size:12px; color:var(--ink-dim); font-style:italic; }}
    .pick-name {{ font-size:13.5px; font-weight:600; margin-bottom:3px; color:var(--ink); }}
    .pick-odd {{ font-size:20px; font-weight:800; font-variant-numeric:tabular-nums; margin-bottom:6px; color:var(--ink); line-height:1; }}
    .pick-stats {{ display:flex; justify-content:space-between; font-size:11.5px; color:var(--ink-soft); }}
    .pick-stats .edge {{ color:var(--safe-strong); font-weight:700; }}
    .risky .pick-stats .edge {{ color:var(--risky-strong); }}
    .ultra .pick-stats .edge {{ color:var(--ultra-strong); }}
    .pick-detail {{ display:flex; justify-content:space-between; font-size:11px; color:var(--ink-dim); margin-top:3px; }}
    .pick-note {{ font-size:11px; color:var(--ink-soft); margin-top:5px; padding-top:5px; border-top:1px dashed var(--line); }}
    .value-tag {{ display:inline-block; padding:1px 6px; border-radius:3px; font-size:9px; font-weight:800; letter-spacing:.6px; }}
    .value-tag.yes {{ background:var(--yellow-card); color:var(--ink); }}
    .value-tag.no {{ background:transparent; color:var(--ink-dim); border:1px solid var(--line); font-weight:600; }}
    .combos {{ margin-top:18px; display:grid; grid-template-columns: 1fr 1fr 1fr; gap:12px; }}
    .combo {{ background:var(--white); border:1px solid var(--line); border-top:5px solid var(--pitch); border-radius:8px; padding:16px 18px; box-shadow: 0 2px 6px rgba(14,26,19,.06); }}
    .combo.safe {{ border-top-color:var(--safe); background: linear-gradient(180deg, var(--safe-soft) 0%, var(--white) 60%); }}
    .combo.risky {{ border-top-color:var(--risky); background: linear-gradient(180deg, var(--risky-soft) 0%, var(--white) 60%); }}
    .combo.ultra {{ border-top-color:var(--ultra); background: linear-gradient(180deg, var(--ultra-soft) 0%, var(--white) 60%); }}
    .combo h3 {{ margin:0 0 10px; font-size:14px; font-weight:800; display:flex; align-items:center; gap:10px; color:var(--ink); }}
    .combo .combo-meta {{ margin-left:auto; font-size:11px; font-weight:600; color:var(--ink-dim); background: var(--pitch-light); padding:3px 9px; border-radius:10px; }}
    .combo ul {{ margin:0 0 12px; padding:0; list-style:none; font-size:12.5px; }}
    .combo li {{ display:flex; justify-content:space-between; gap:10px; padding:6px 0; border-bottom:1px dashed var(--line); align-items:center; }}
    .combo li:last-child {{ border-bottom:none; }}
    .combo-match {{ color:var(--ink-soft); flex:1; }}
    .combo-time {{ display:inline-block; background:var(--pitch); color:var(--white); font-size:10px; font-weight:700; padding:1px 6px; border-radius:3px; margin-right:7px; font-variant-numeric:tabular-nums; }}
    .combo-pick {{ color:var(--ink); font-variant-numeric:tabular-nums; display:inline-flex; align-items:center; gap:8px; }}
    .combo-tag {{ font-size:9px; font-weight:800; letter-spacing:.5px; padding:2px 5px; border-radius:3px; }}
    .tag-value {{ background:var(--yellow-card); color:var(--ink); }}
    .combo-excluded {{ font-size:11.5px; color:var(--ink-dim); background:#fff7e0; padding:8px 11px; border-radius:4px; margin-bottom:10px; }}
    .combo-excluded ul {{ margin:6px 0 0; padding-left:18px; font-size:11px; }}
    .combo-excluded li {{ display:list-item; padding:2px 0; border:none; }}
    .combo .total {{ font-size:12.5px; font-weight:600; padding-top:10px; border-top:2px solid var(--pitch); display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; align-items:baseline; }}
    .combo.safe .total {{ border-top-color: var(--safe); }}
    .combo.risky .total {{ border-top-color: var(--risky); }}
    .combo.ultra .total {{ border-top-color: var(--ultra); }}
    .combo .total-main {{ flex:1; }}
    .combo .cote {{ font-size:24px; font-weight:900; font-variant-numeric:tabular-nums; color:var(--pitch-dark); margin-left:6px; }}
    .combo.safe .cote {{ color: var(--safe-strong); }}
    .combo.risky .cote {{ color: var(--risky-strong); }}
    .combo.ultra .cote {{ color: var(--ultra-strong); }}
    .combo .payout {{ background:var(--yellow-card); color:var(--ink); padding:4px 9px; border-radius:4px; font-weight:700; font-size:12px; }}
    .combo.empty {{ color:var(--ink-dim); }}
    .combo.empty .empty-msg {{ font-style:italic; padding:12px 0; text-align:center; }}
    footer {{ padding:22px 36px 36px; color:var(--ink-dim); font-size:12px; border-top:1px solid var(--line); text-align:center; background:var(--pitch-light); margin-top:32px; }}
    .tabs {{
      display:flex; gap:4px; padding: 14px 36px 0; max-width:1240px; margin: 0 auto;
      border-bottom: 2px solid var(--line);
    }}
    .tab {{
      background: transparent; border:0; padding:10px 18px; cursor:pointer;
      font-size:13.5px; font-weight:600; color: var(--ink-dim);
      border-bottom: 3px solid transparent; margin-bottom:-2px;
      transition: color .12s, border-color .12s;
    }}
    .tab:hover {{ color: var(--ink); }}
    .tab.active {{ color: var(--pitch); border-bottom-color: var(--pitch); }}
    .tab-content {{ display:none; }}
    .tab-content.active {{ display:block; }}
    .perf-tools {{ display:flex; gap:14px; align-items:center; padding:14px 0 4px; flex-wrap:wrap; }}
    .refresh-btn.small {{ padding:7px 14px; font-size:12.5px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
    .perf-note {{ font-size: 11.5px; color: var(--ink-dim); margin-left:auto; }}
    .grand-total {{
      display:grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 14px 0 20px;
      background: linear-gradient(135deg, var(--pitch-light), var(--white));
      border:1px solid var(--line); border-radius:8px; padding:18px 22px;
    }}
    .grand-total > div {{ display:flex; flex-direction:column; gap:4px; text-align:center; }}
    .gt-num {{ font-size: 26px; font-weight:800; font-variant-numeric:tabular-nums; }}
    .gt-lbl {{ font-size:11.5px; color:var(--ink-dim); text-transform:uppercase; letter-spacing:.5px; }}
    .gt-num.gain {{ color: var(--safe); }}
    .gt-num.loss {{ color: var(--risky); }}
    .perf-summary {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 20px; }}
    .perf-card {{ background: var(--white); border:1px solid var(--line); border-left:5px solid var(--pitch); border-radius:8px; padding:14px 18px; }}
    .perf-card.safe {{ border-left-color: var(--safe); }}
    .perf-card.risky {{ border-left-color: var(--risky); }}
    .perf-card.ultra {{ border-left-color: var(--ultra); }}
    .perf-card-head {{ font-size:13px; font-weight:700; color: var(--ink); margin-bottom:10px; }}
    .perf-card-pl {{ display:flex; gap:14px; align-items:baseline; margin-bottom:10px; flex-wrap:wrap; }}
    .pl-amount {{ font-size: 22px; font-weight:800; font-variant-numeric: tabular-nums; }}
    .pl-roi {{ font-size: 13px; font-weight:700; }}
    .gain {{ color: var(--safe); }} .loss {{ color: var(--risky); }}
    .perf-card-stats {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:8px; padding-top:8px; border-top:1px dashed var(--line); }}
    .perf-card-stats > div {{ display:flex; flex-direction:column; align-items:center; }}
    .stat-num {{ font-size:16px; font-weight:700; color: var(--ink); }}
    .stat-lbl {{ font-size:10px; color: var(--ink-dim); text-transform:uppercase; letter-spacing:.4px; }}
    .standings {{ background:var(--white); border:1px solid var(--line); border-radius:8px; padding:14px 18px; margin-bottom:20px; }}
    .standings h3 {{ margin:0 0 10px; font-size: 14px; }}
    .standings-table, .history-table {{ width:100%; border-collapse: collapse; font-size: 12.5px; }}
    .standings-table th, .standings-table td, .history-table th, .history-table td {{
      padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--line);
    }}
    .standings-table th, .history-table th {{ background: var(--pitch-light); font-size:11px; text-transform:uppercase; letter-spacing:.4px; color:var(--ink-soft); }}
    .standings-table .gd, .standings-table .pts {{ text-align:right; font-variant-numeric: tabular-nums; }}
    .standings-table .pts {{ font-weight:700; color: var(--pitch-dark); }}
    .standings.empty, .history.empty {{ padding: 24px; text-align:center; color: var(--ink-dim); font-style:italic; background:var(--pitch-light); border-radius:8px; border:1px solid var(--line); }}
    .history {{ background:var(--white); border:1px solid var(--line); border-radius:8px; padding:14px 18px; }}
    .history h3 {{ margin:0 0 10px; font-size: 14px; }}
    .history-table td.date {{ color: var(--ink-soft); font-variant-numeric: tabular-nums; white-space:nowrap; }}
    .history-table td.match {{ font-weight: 500; }}
    .history-table td.cat {{ text-align:center; font-size:14px; }}
    .history-table td.cote, .history-table td.stake, .history-table td.result {{ font-variant-numeric: tabular-nums; }}
    .history-table td.status {{ font-weight:700; text-align:center; white-space: nowrap; }}
    .history-table td.outcome-won {{ color: var(--safe); }}
    .history-table td.outcome-lost {{ color: var(--risky); }}
    .history-table td.outcome-pending {{ color: var(--ink-dim); }}
    .history-table td.outcome-manual {{ color: var(--ultra-strong); }}
    .history-table tr.row-won {{ background: rgba(46,160,67,.04); }}
    .history-table tr.row-lost {{ background: rgba(196,68,26,.04); }}
    .history-table td.pl {{ text-align:right; font-weight:600; font-variant-numeric: tabular-nums; }}
    .leg-details {{ margin-top: 20px; background: var(--white); border:1px solid var(--line); border-radius: 8px; }}
    .leg-details > summary {{ padding: 12px 18px; cursor:pointer; font-size: 13px; font-weight: 600; color: var(--ink-soft); list-style: none; }}
    .leg-details > summary::-webkit-details-marker {{ display: none; }}
    .leg-details > summary::before {{ content: "▸ "; }}
    .leg-details[open] > summary::before {{ content: "▾ "; }}
    .leg-details .history {{ border: none; margin: 0; padding: 0 18px 18px; }}
    .combo-history-table td.legs {{ font-size: 11.5px; color: var(--ink-soft); }}
    .mini-btn {{ font-size:10px; padding: 2px 6px; border:1px solid var(--line); background:var(--white); border-radius:3px; cursor:pointer; margin-left:4px; font-weight:700; }}
    .mini-btn.mini-win:hover {{ background: var(--safe-soft); color: var(--safe-strong); border-color: var(--safe); }}
    .mini-btn.mini-lose:hover {{ background: var(--risky-soft); color: var(--risky-strong); border-color: var(--risky); }}
    @media (max-width: 980px) {{
      .picks.three, .combos, .perf-summary, .grand-total {{ grid-template-columns: 1fr; }}
      header, .legend, .transparency, main, footer, details.explainer, .tabs {{ padding-left:16px; padding-right:16px; }}
      .header-actions {{ position:static; margin-top:14px; }}
      .history-table {{ font-size: 11px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>⚽ Paris du jour <span class="badge">UNIBET</span></h1>
    <div class="sub">
      <span class="pill">{html.escape(window_label)}</span>
      <span class="pill">Mise: 10€ safe · 8€ risqué · 2€ ultra</span>
      <span class="pill">Mise à jour {fetched_local}</span>
      <span class="pill">API : {cu}/500 utilisés · {cr} restants</span>
    </div>
    <div class="header-actions">
      <button id="refresh-btn" class="refresh-btn" onclick="doRefresh()">🔄 Rafraîchir les paris du jour</button>
      <span id="refresh-status" class="refresh-status"></span>
    </div>
  </header>

  <div class="legend">
    <span>🛡️ <b>SAFE</b> (10€) cap cote ≤ {SAFE_COMBO_CAP:.0f}</span>
    <span>⚡ <b>RISQUÉ</b> (8€) cap cote ≤ {RISKY_COMBO_CAP:.0f}</span>
    <span>🎲 <b>ULTRA-RISQUÉ</b> (2€) sans limite, inclut buteurs</span>
    <span>★ <b>VALEUR</b> = edge ≥ 2/5/5%</span>
    <span>🔥 en forme · ● stable · ❄️ méforme</span>
  </div>

  <div class="transparency">
    <strong>Sources</strong> ·
    <span><b>Cotes 1X2 & Over/Under</b> : Unibet (variantes FR/NL/SE/UK via The Odds API).</span>
    <span><b>Cotes buteur anytime</b> : non exposées par Unibet sur cette API → cote indicative du consensus marché (Pinnacle, William Hill, Sky Bet, Betfair). Vérifier la cote finale sur unibet.be avant de jouer.</span>
    <span><b>Modèle</b> : Poisson + Dixon-Coles sur Elo (avec ajustement de forme) ; consensus marché dévigué pour la blend équitable.</span>
  </div>

  <details class="explainer">
    <summary>❓ <b>Comment je calcule l'EV ?</b> — pipeline complet (cliquer pour ouvrir)</summary>
    <div class="explainer-body">
      <p>L'<b>EV</b> mesure le gain moyen attendu par euro misé si on rejouait le même pari un grand nombre de fois.</p>
      <div class="formula" style="font-size:14px;font-weight:600;">EV = (proba équitable × cote) − 1</div>

      <h4>① Probabilité « modèle » — Poisson + Dixon-Coles à partir de l'Elo (+ forme pré-tournoi + déroulé)</h4>
      <p>Le multiplicateur de forme appliqué à chaque λ combine désormais <b>deux signaux</b> :</p>
      <ul>
        <li><b>Forme pré-tournoi</b> : écart vs pic Elo des 12 derniers mois (capté du fichier eloratings.net), borné ±7%.</li>
        <li><b>Forme du déroulé tournoi</b> : une fois 1 à 3 matchs joués, on compare la <b>différence de buts réelle par match</b> à celle attendue par l'Elo. Une équipe qui surperforme (gros score contre meilleur équipe que prévu) gagne jusqu'à +6% sur son λ ; sous-performance jusqu'à −6%. Le poids du signal grandit jusqu'à 3 matchs joués (≤3 matchs = signal partiel ; ≥3 = pleinement pris en compte).</li>
      </ul>
      <p>Les deux multiplicateurs sont composés (≤ ±10% global). C'est ainsi que le modèle s'adapte automatiquement au déroulé du tournoi à chaque fois que tu cliques « Mettre à jour les résultats » dans l'onglet Performance.</p>
      <p>elo_diff = elo_home + avantage_hôte − elo_away ; goal_diff = elo_diff/200 ; λ_team = (2,45 ± goal_diff)/2. Pour Canada–Bosnie : λ_Canada ≈ 1,86 ; λ_Bosnie ≈ 0,59. Ces λ sont multipliés par form_mult (1±7%) selon l'écart au pic Elo 12 mois. La matrice des scores P(i,j) est construite par Poisson croisée + Dixon-Coles (ρ=−0,13). On somme les cellules correspondant à chaque issue pour obtenir P_modèle.</p>

      <h4>② Probabilité « consensus marché »</h4>
      <p>On prend la médiane des cotes des ~12 bookmakers EU/UK, on dé-vigue : p_devig = (1/cote) / Σ(1/cote_j).</p>

      <h4>③ Probabilité « équitable »</h4>
      <div class="formula">P_équitable = 0,40 × P_modèle + 0,60 × P_consensus</div>

      <h4>④ EV vs cote Unibet (avec plafond bon sens)</h4>
      <div class="formula">EV_brut = P_équitable × cote_Unibet − 1</div>
      <p><b>Plafond d'edge à +30%.</b> Un edge brut supérieur à +30% reflète quasi toujours un biais de modèle (le marché est très bien calibré sur les cotes extrêmes parce que beaucoup d'argent passe dessus), pas une vraie inefficience. Quand on dépasse, on ramène l'edge affiché à +30% et on re-dérive une P_équitable cohérente :</p>
      <div class="formula">si EV_brut &gt; 30 % :<br>
&nbsp;&nbsp;P_équitable = 1,30 / cote ;&nbsp;&nbsp;EV affichée = +30 %</div>
      <p>De plus, P_équitable ne peut jamais dépasser <b>1,3 × P_consensus marché</b>. Si le modèle pense qu'une issue est deux fois plus probable que le marché, c'est presque toujours le modèle qui se trompe (motivation, blessure, info compo que le marché a, lui).</p>

      <h4>Cas buteur (ultra-risqué)</h4>
      <p>Pour un joueur, P_modèle = 1 − exp(−λ_équipe × part_du_joueur). La cote vient du consensus marché (Unibet n'exposant pas ce marché via l'API). L'EV affichée utilise donc P_modèle vs cote consensus, considéré comme proxy raisonnable de la cote Unibet finale.</p>

      <h4>Combiné</h4>
      <div class="formula">cote_combo = ∏ cote_i ; P_combo = ∏ P_i ; EV_combo = P_combo × cote_combo − 1</div>
      <p>Les combinés safe/risqué ont des plafonds (5 et 25) : si le produit naturel dépasse, l'algorithme retient le sous-ensemble de picks dont le produit est le plus élevé sous le cap.</p>

      <p class="warn"><b>⚠️ Variance.</b> EV positif ≠ gain garanti. Sur un combiné à proba 3% et EV +50%, on perd 97% des fois — c'est mathématiquement attendu. Stakes fixes (10/8/2€) limitent l'exposition.</p>
      <p class="warn"><b>⚠️ Limites.</b> Pas de stats joueur granulaires (xG), pas de blessures/compos, pas de météo. Forme = écart Elo vs pic 12 mois (proxy). Joueurs cadres affichés à titre indicatif.</p>
    </div>
  </details>

  <div class="tabs">
    <button class="tab active" data-tab="bets" onclick="showTab('bets')">⚽ Paris du jour</button>
    <button class="tab" data-tab="perf" onclick="showTab('perf')">📈 Performance</button>
  </div>

  <main>
    <div class="tab-content active" data-tab="bets">
      {body}
    </div>
    <div class="tab-content" data-tab="perf">
      {render_performance_tab(perf, combos, combo_totals, standings)}
    </div>
  </main>

  <footer>
    Outil d'analyse, à fins informatives. Le pari sportif comporte des risques de perte financière.<br>
    Cotes Unibet via The Odds API · Buteur via consensus marché · Elo eloratings.net · Heure belge (CEST).
  </footer>

  <script>
    function showTab(name) {{
      document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.dataset.tab === name));
      try {{ history.replaceState(null, '', '#' + name); }} catch(e) {{}}
    }}
    // Restore tab from URL hash
    if (location.hash === '#perf') showTab('perf');

    async function doRefresh() {{
      const btn = document.getElementById('refresh-btn');
      const status = document.getElementById('refresh-status');
      btn.disabled = true;
      status.textContent = '⏳ Rafraîchissement en cours…';
      try {{
        const r = await fetch('/refresh', {{method: 'POST'}});
        const j = await r.json();
        if (j.status === 'ok') {{
          status.textContent = '✓ Terminé, rechargement…';
          setTimeout(() => location.reload(), 400);
        }} else {{
          status.textContent = '⚠ Erreur: ' + (j.detail || 'inconnue');
          btn.disabled = false;
        }}
      }} catch (e) {{
        status.textContent = '⚠ Pas de serveur local (lance `python3 serve.py`).';
        btn.disabled = false;
      }}
    }}

    async function updateResults() {{
      const status = document.getElementById('update-status');
      status.textContent = '⏳ Récupération des scores…';
      try {{
        const r = await fetch('/update-results', {{method: 'POST'}});
        const j = await r.json();
        if (j.status === 'ok') {{
          status.textContent = `✓ ${{j.n_new || 0}} nouveaux, ${{j.n_updated || 0}} mis à jour`;
          setTimeout(() => location.reload(), 600);
        }} else {{
          status.textContent = '⚠ Erreur: ' + (j.detail || 'inconnue');
        }}
      }} catch (e) {{
        status.textContent = '⚠ Pas de serveur local.';
      }}
    }}

    async function markOutcome(eventId, category, outcome) {{
      try {{
        const r = await fetch('/mark-outcome', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{event_id: eventId, category, outcome}})
        }});
        const j = await r.json();
        if (j.status === 'ok') location.reload();
      }} catch (e) {{
        alert('Erreur de marquage');
      }}
    }}
  </script>
</body>
</html>"""


def main() -> None:
    analysis = analyse_today()
    html_out = render(analysis)
    OUT.write_text(html_out, encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Matches in today's window: {len(analysis['matches'])}")
    print(f"Credits used: {analysis['credits_used']}, remaining: {analysis['credits_remaining']}")


if __name__ == "__main__":
    main()
