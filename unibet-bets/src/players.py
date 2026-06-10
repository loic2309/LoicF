"""
Per-team marquee scorers + their estimated share of team goals.

`scorer_share` is the fraction of the team's expected goals attributed to
this specific player as an "anytime goalscorer" estimate. Used to translate
the team-level λ from the Poisson model into a per-player probability of
scoring at least once:

    P(player scores ≥ 1) = 1 − exp(−λ_team × share)

Shares are calibrated against publicly known club-level form for the
2025-26 season + national-team goal contribution over the last qualifying
cycle. They are rough — purely a sanity proxy, never a precise model.
"""

from __future__ import annotations

# {team_name: [(player_name_as_it_appears_in_odds_feeds, scorer_share)]}
# Player names are normalized to the form most Odds-API bookmakers return.
TOP_SCORERS = {
    "Argentina":             [("Lionel Messi", 0.30), ("Lautaro Martinez", 0.28), ("Julian Alvarez", 0.22)],
    "Brazil":                [("Vinicius Junior", 0.28), ("Raphinha", 0.24), ("Rodrygo", 0.20)],
    "France":                [("Kylian Mbappe", 0.40), ("Ousmane Dembele", 0.22), ("Marcus Thuram", 0.18)],
    "Spain":                 [("Lamine Yamal", 0.24), ("Alvaro Morata", 0.22), ("Mikel Oyarzabal", 0.20)],
    "England":               [("Harry Kane", 0.36), ("Bukayo Saka", 0.24), ("Jude Bellingham", 0.22)],
    "Portugal":              [("Cristiano Ronaldo", 0.28), ("Bruno Fernandes", 0.22), ("Rafael Leao", 0.20)],
    "Netherlands":           [("Cody Gakpo", 0.26), ("Memphis Depay", 0.22), ("Donyell Malen", 0.18)],
    "Germany":               [("Florian Wirtz", 0.22), ("Niclas Fullkrug", 0.22), ("Kai Havertz", 0.20)],
    "Belgium":               [("Romelu Lukaku", 0.32), ("Kevin De Bruyne", 0.18), ("Jeremy Doku", 0.18)],
    "Croatia":               [("Andrej Kramaric", 0.24), ("Bruno Petkovic", 0.20), ("Luka Modric", 0.16)],
    "Norway":                [("Erling Haaland", 0.48), ("Alexander Sorloth", 0.22), ("Martin Odegaard", 0.16)],
    "Uruguay":               [("Darwin Nunez", 0.26), ("Federico Valverde", 0.20), ("Maxi Araujo", 0.16)],
    "Colombia":              [("Luis Diaz", 0.26), ("Jhon Duran", 0.22), ("James Rodriguez", 0.18)],
    "Switzerland":           [("Breel Embolo", 0.24), ("Dan Ndoye", 0.20), ("Zeki Amdouni", 0.18)],
    "Japan":                 [("Takefusa Kubo", 0.24), ("Daichi Kamada", 0.20), ("Ayase Ueda", 0.20)],
    "South Korea":           [("Son Heung-min", 0.32), ("Hwang Hee-chan", 0.22), ("Lee Kang-in", 0.20)],
    "Mexico":                [("Santiago Gimenez", 0.28), ("Raul Jimenez", 0.22), ("Hirving Lozano", 0.20)],
    "USA":                   [("Christian Pulisic", 0.26), ("Folarin Balogun", 0.22), ("Ricardo Pepi", 0.20)],
    "Canada":                [("Jonathan David", 0.32), ("Cyle Larin", 0.22), ("Alphonso Davies", 0.16)],
    "Senegal":               [("Sadio Mane", 0.26), ("Nicolas Jackson", 0.22), ("Iliman Ndiaye", 0.20)],
    "Morocco":               [("Brahim Diaz", 0.22), ("Hakim Ziyech", 0.20), ("Youssef En-Nesyri", 0.22)],
    "Ivory Coast":           [("Sebastien Haller", 0.28), ("Nicolas Pepe", 0.20), ("Simon Adingra", 0.18)],
    "Algeria":               [("Riyad Mahrez", 0.26), ("Mohamed Amoura", 0.24), ("Baghdad Bounedjah", 0.18)],
    "Egypt":                 [("Mohamed Salah", 0.42), ("Omar Marmoush", 0.22), ("Mostafa Mohamed", 0.16)],
    "Tunisia":               [("Hannibal Mejbri", 0.22), ("Wahbi Khazri", 0.18), ("Naim Sliti", 0.18)],
    "Ghana":                 [("Mohammed Kudus", 0.26), ("Antoine Semenyo", 0.22), ("Inaki Williams", 0.20)],
    "Australia":             [("Mitchell Duke", 0.22), ("Jamie Maclaren", 0.20), ("Awer Mabil", 0.18)],
    "Iran":                  [("Mehdi Taremi", 0.32), ("Sardar Azmoun", 0.24), ("Mehdi Ghayedi", 0.16)],
    "Saudi Arabia":          [("Salem Al-Dawsari", 0.24), ("Firas Al-Buraikan", 0.22), ("Saleh Al-Shehri", 0.20)],
    "Czech Republic":        [("Patrik Schick", 0.28), ("Adam Hlozek", 0.20), ("Mojmir Chytil", 0.18)],
    "Austria":               [("Marko Arnautovic", 0.24), ("Michael Gregoritsch", 0.22), ("Marcel Sabitzer", 0.20)],
    "Sweden":                [("Alexander Isak", 0.32), ("Viktor Gyokeres", 0.30), ("Dejan Kulusevski", 0.18)],
    "Scotland":              [("Che Adams", 0.24), ("Lyndon Dykes", 0.20), ("John McGinn", 0.18)],
    "Paraguay":              [("Antonio Sanabria", 0.26), ("Diego Gonzalez", 0.20), ("Julio Enciso", 0.18)],
    "Ecuador":               [("Enner Valencia", 0.30), ("Kendry Paez", 0.20), ("Felix Torres", 0.16)],
    "Bosnia & Herzegovina":  [("Edin Dzeko", 0.32), ("Ermedin Demirovic", 0.24), ("Edin Visca", 0.18)],
    "Panama":                [("Jose Fajardo", 0.24), ("Cesar Yanis", 0.22), ("Ismael Diaz", 0.20)],
    "Iraq":                  [("Aymen Hussein", 0.26), ("Ali Al-Hamadi", 0.24), ("Mohanad Ali", 0.20)],
    "Jordan":                [("Musa Al-Taamari", 0.30), ("Yazan Al-Naimat", 0.22), ("Ali Olwan", 0.18)],
    "Uzbekistan":            [("Eldor Shomurodov", 0.32), ("Igor Sergeev", 0.22), ("Abbosbek Fayzullaev", 0.18)],
    "South Africa":          [("Lyle Foster", 0.26), ("Percy Tau", 0.22), ("Themba Zwane", 0.18)],
    "Cape Verde":            [("Jovane Cabral", 0.22), ("Garry Rodrigues", 0.20), ("Bebe", 0.18)],
    "DR Congo":              [("Yoane Wissa", 0.26), ("Silas Katompa", 0.22), ("Cedric Bakambu", 0.20)],
    "New Zealand":           [("Chris Wood", 0.36), ("Kosta Barbarouses", 0.20), ("Elijah Just", 0.18)],
    "Haiti":                 [("Duckens Nazon", 0.24), ("Frantzdy Pierrot", 0.22), ("Mondy Prunier", 0.18)],
    "Curacao":               [("Tahith Chong", 0.24), ("Leandro Bacuna", 0.20), ("Juriën Gaari", 0.16)],
    "Curaçao":               [("Tahith Chong", 0.24), ("Leandro Bacuna", 0.20), ("Juriën Gaari", 0.16)],
    "Qatar":                 [("Almoez Ali", 0.28), ("Akram Afif", 0.26), ("Hassan Al-Haydos", 0.18)],
}


def player_score_prob(team_lambda: float, share: float) -> float:
    """Anytime goalscorer probability from team λ × player share."""
    import math
    player_lambda = team_lambda * share
    return 1.0 - math.exp(-player_lambda)


def scorers_for(team: str) -> list[tuple[str, float]]:
    return TOP_SCORERS.get(team, [])
