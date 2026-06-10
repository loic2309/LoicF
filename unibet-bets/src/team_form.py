"""
Team-level qualitative + form data on top of pure Elo.

What this module gives the rest of the pipeline:
  - peak_gap_1y    : current Elo minus the team's peak Elo in the last
                     12 months (col4 - col6 of the eloratings TSV) — always
                     ≤ 0. Closer to zero = team currently at/near peak form;
                     deeply negative = significant slump vs recent peak.
  - form_class     : "in_form" (gap ≥ -40)
                     "stable"  (gap -40 to -150)
                     "slump"   (gap < -150)
  - form_mult      : multiplicative λ adjustment derived from peak_gap,
                     clamped to [0.93, 1.07] — applied on top of the Elo-
                     derived expected goals so teams in form score slightly
                     more, teams in slump slightly less.
  - key_players    : 1-2 marquee players per team (hardcoded, qualitative)

We could not get a clean "last N matches goals scored/conceded" feed in bulk
from any static public source (eloratings.net is JS-rendered; FIFA ranking
doesn't expose recent stats; Wikipedia qualification pages aggregate only).
The peak-gap signal is the best proxy available without burning Odds-API
credits or building a fragile per-team scraper for 48 endpoints.
"""

from pathlib import Path

from team_codes import TEAM_TO_CODE

DATA_DIR = Path(__file__).parent.parent / "data"


def load_elo_full() -> dict:
    """
    Returns {team_name: {"elo": int, "peak_gap_1y": int, "form_class": str,
                         "form_mult": float}}.

    TSV column layout (eloratings.net World.tsv):
      col1=rank, col2=avg_rank, col3=code, col4=current_rating,
      col5=peak_rank_1y,  col6=peak_rating_1y,
      col7=peak_rank_5y,  col8=peak_rating_5y,
      col9=peak_rank_10y, col10=peak_rating_10y,
      ...changes/history columns 11-22,
      col23..31 = career totals (matches, W, D, L, GF, GA, ...)
    """
    by_code = {}
    with open(DATA_DIR / "elo_raw.tsv", "r") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            try:
                code = parts[2]
                cur = int(parts[3])
                peak_1y = int(parts[5])
                by_code[code] = {"elo": cur, "peak_gap_1y": cur - peak_1y}
            except (ValueError, IndexError):
                continue

    out = {}
    for team, code in TEAM_TO_CODE.items():
        if code not in by_code:
            continue
        rec = by_code[code]
        gap = rec["peak_gap_1y"]
        if gap >= -40:
            form_class = "in_form"
        elif gap >= -150:
            form_class = "stable"
        else:
            form_class = "slump"

        # Translate gap into a multiplicative λ adjustment. The mapping is
        # gentle: a 100-point peak gap shifts λ by ~2.5%. Clamped so even
        # the deepest slump caps at -7%.
        raw_mult = 1.0 + max(gap, -300) / 4000.0
        form_mult = max(0.93, min(1.07, raw_mult))

        out[team] = {
            "elo": rec["elo"],
            "peak_gap_1y": gap,
            "form_class": form_class,
            "form_mult": round(form_mult, 4),
        }
    return out


# Marquee / in-form players per WC team. Hardcoded snapshot as of June 2026
# kick-off. Used only for qualitative display, never feeds the quantitative
# model. Sources: FIFA national team squads, top club scorers 2025-26.
KEY_PLAYERS = {
    "Argentina":             ["Lionel Messi (Inter Miami)", "Lautaro Martínez (Inter)"],
    "Brazil":                ["Vinícius Jr. (Real Madrid)", "Raphinha (Barcelona)"],
    "France":                ["Kylian Mbappé (Real Madrid)", "Ousmane Dembélé (PSG)"],
    "Spain":                 ["Lamine Yamal (Barcelona)", "Pedri (Barcelona)"],
    "England":               ["Jude Bellingham (Real Madrid)", "Bukayo Saka (Arsenal)"],
    "Portugal":              ["Cristiano Ronaldo (Al-Nassr)", "Bruno Fernandes (Man United)"],
    "Netherlands":           ["Cody Gakpo (Liverpool)", "Virgil van Dijk (Liverpool)"],
    "Germany":               ["Florian Wirtz (Bayern)", "Jamal Musiala (Bayern)"],
    "Belgium":               ["Kevin De Bruyne (Napoli)", "Romelu Lukaku (Napoli)"],
    "Italy":                 ["Nicolò Barella (Inter)", "Federico Chiesa (Liverpool)"],
    "Croatia":               ["Luka Modrić (Milan)", "Joško Gvardiol (Man City)"],
    "Norway":                ["Erling Haaland (Man City)", "Martin Ødegaard (Arsenal)"],
    "Uruguay":               ["Federico Valverde (Real Madrid)", "Darwin Núñez (Liverpool)"],
    "Colombia":              ["Luis Díaz (Bayern)", "James Rodríguez (Club León)"],
    "Switzerland":           ["Granit Xhaka (Leverkusen)", "Manuel Akanji (Man City)"],
    "Japan":                 ["Takefusa Kubo (Real Sociedad)", "Wataru Endō (Liverpool)"],
    "South Korea":           ["Son Heung-min (LAFC)", "Lee Kang-in (PSG)"],
    "Mexico":                ["Edson Álvarez (West Ham)", "Santiago Giménez (Milan)"],
    "USA":                   ["Christian Pulisic (Milan)", "Weston McKennie (Juventus)"],
    "Canada":                ["Alphonso Davies (Bayern)", "Jonathan David (Juventus)"],
    "Senegal":               ["Sadio Mané (Al-Nassr)", "Nicolas Jackson (Chelsea)"],
    "Morocco":               ["Achraf Hakimi (PSG)", "Brahim Díaz (Real Madrid)"],
    "Ivory Coast":           ["Sébastien Haller (Utrecht)", "Franck Kessié (Al-Ahli)"],
    "Algeria":               ["Riyad Mahrez (Al-Ahli)", "Ismaël Bennacer (Marseille)"],
    "Egypt":                 ["Mohamed Salah (Liverpool)", "Omar Marmoush (Man City)"],
    "Tunisia":               ["Hannibal Mejbri (Burnley)", "Wahbi Khazri (free)"],
    "Ghana":                 ["Mohammed Kudus (Tottenham)", "Antoine Semenyo (Bournemouth)"],
    "Australia":             ["Mathew Ryan (AZ Alkmaar)", "Riley McGree (Middlesbrough)"],
    "Iran":                  ["Mehdi Taremi (Inter)", "Sardar Azmoun (Shabab Al-Ahli)"],
    "Saudi Arabia":          ["Salem Al-Dawsari (Al-Hilal)", "Salman Al-Faraj (Al-Hilal)"],
    "Czech Republic":        ["Patrik Schick (Leverkusen)", "Tomáš Souček (West Ham)"],
    "Austria":               ["Marcel Sabitzer (Dortmund)", "David Alaba (Real Madrid)"],
    "Sweden":                ["Alexander Isak (Newcastle)", "Viktor Gyökeres (Arsenal)"],
    "Scotland":              ["Scott McTominay (Napoli)", "Andrew Robertson (Liverpool)"],
    "Paraguay":              ["Miguel Almirón (Atlanta United)", "Antonio Sanabria (Cremonese)"],
    "Ecuador":               ["Moisés Caicedo (Chelsea)", "Enner Valencia (Internacional)"],
    "Bosnia & Herzegovina":  ["Edin Džeko (Fenerbahçe)", "Miralem Pjanić (CSKA Moscow)"],
    "Panama":                ["Aníbal Godoy (San Jose)", "José Fajardo (San Lorenzo)"],
    "Iraq":                  ["Aymen Hussein (Al-Najaf)", "Ali Al-Hamadi (Ipswich)"],
    "Jordan":                ["Musa Al-Taamari (Montpellier)", "Yazan Al-Naimat (Al-Sadd)"],
    "Uzbekistan":            ["Eldor Shomurodov (Roma)", "Abbosbek Fayzullaev (CSKA Moscow)"],
    "South Africa":          ["Percy Tau (Al-Ahly)", "Lyle Foster (Burnley)"],
    "Cape Verde":            ["Ryan Mendes (Boavista)", "Jovane Cabral (Lazio)"],
    "DR Congo":              ["Cédric Bakambu (Real Betis)", "Yoane Wissa (Brentford)"],
    "New Zealand":           ["Chris Wood (Nottingham Forest)", "Marko Stamenić (Olympiacos)"],
    "Haiti":                 ["Duckens Nazon (Levski Sofia)", "Carlens Arcus (Auxerre)"],
    "Curaçao":               ["Leandro Bacuna (Cardiff City)", "Tahith Chong (Sheffield United)"],
    "Qatar":                 ["Akram Afif (Al-Sadd)", "Almoez Ali (Al-Duhail)"],
}


def get_key_players(team: str) -> list[str]:
    return KEY_PLAYERS.get(team, [])
