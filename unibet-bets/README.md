# Paris Coupe du Monde 2026 · Unibet

Outil personnel d'analyse statistique pour le pari sportif sur la Coupe du Monde FIFA 2026.

Pour chaque match du jour (fenêtre 15h → 06h heure belge) le pipeline génère trois paris recommandés — **safe** (10 € de mise, cote combinée du jour plafonnée à 5), **risqué** (8 €, plafond 25) et **ultra-risqué** (2 €, sans plafond, inclut les buteurs) — et un onglet Performance trace les résultats au fil du tournoi.

## Stack

Python 3.9+ pur (stdlib uniquement, pas de dépendances pip). Sortie : page HTML statique. Serveur local optionnel pour le bouton refresh.

## Méthodologie

Le modèle quantitatif est un **Poisson + Dixon-Coles bivarié** calibré sur :

1. **Ratings Elo** des sélections (eloratings.net, fichier `World.tsv`) — le plus prédictif single-factor en football international.
2. **Forme pré-tournoi** : écart vs pic Elo des 12 derniers mois → multiplicateur sur λ ∈ [0.93, 1.07].
3. **Forme du déroulé** : une fois 1-3 matchs joués au tournoi, comparaison de la différence de buts réelle vs attendue par Elo → multiplicateur ∈ [0.94, 1.06]. Combiné avec la forme pré-tournoi (cap global ±10%).
4. **Avantage hôte** (USA / Canada / Mexique) : +60 Elo sur les matchs à domicile.
5. **Consensus marché dévigué** (médiane des cotes des ~12 bookmakers EU/UK, débarrassée de la marge bookmaker).

La probabilité « équitable » utilisée pour le calcul d'EV est `0,40 × P_modèle + 0,60 × P_consensus`, avec **deux garde-fous bon sens** :

- `P_équitable ≤ 1,3 × P_consensus` (on ne prétend pas savoir mieux que le marché).
- Edge plafonné à +30 % (au-delà, c'est du bruit de modèle, pas une vraie inefficience).

```
EV = P_équitable × cote_Unibet − 1
```

### Catégories de paris

| Catégorie | Mise | Plage de proba | Cap cote combinée | Marchés |
|---|---|---|---|---|
| 🛡️ Safe | 10 € | ≥ 50 % | 5.0 | 1X2, Over/Under buts |
| ⚡ Risqué | 8 € | 18–50 % | 25.0 | 1X2, Over/Under (compatible avec safe) |
| 🎲 Ultra-risqué | 2 € | < 50 % | sans limite | Buteur anytime, long-shots 1X2/totals |

Le combiné de chaque catégorie est l'optimum sous le plafond : si le produit naturel dépasse, l'algo brute-force la combinaison la plus payante respectant le cap.

### Cohérence safe / risqué / ultra

Les trois picks d'un même match ne se contredisent jamais (ex : pas safe = Victoire A et risqué = Victoire B). La règle de compatibilité couvre 1X2 mutuellement exclusifs, Over X / Under Y impossibles (si X ≥ Y), etc.

## Données

| Source | Données | Coût API |
|---|---|---|
| The Odds API `/odds` (bulk) | h2h + totals pour tous les matchs WC 2026 | 2 crédits/call |
| The Odds API `/odds` (per-event) | Buteur anytime pour les matchs du jour | 1 crédit/match |
| The Odds API `/scores` | Résultats finaux | 2 crédits/call |
| eloratings.net `World.tsv` | Ratings Elo + écart au pic 12 mois | Gratuit |

Budget API estimé sur tout le tournoi : ~150–200 crédits sur 500.

**Limite Unibet** : The Odds API n'expose que `h2h` et `totals` pour Unibet. BTTS, mi-temps, buteurs **ne sont pas dans l'API d'Unibet** — pour la catégorie ultra-risqué les cotes buteur viennent du consensus marché (Pinnacle EU principalement), à vérifier sur unibet.be avant de jouer.

## Installation

```bash
git clone https://github.com/loic2309/LoicF.git
cd LoicF/unibet-bets

cp .env.example .env
# Édite .env et colle ta clé The Odds API
```

Aucune dépendance pip. Python 3.9+ requis (utilise `zoneinfo` de la stdlib).

## Utilisation

### Mode rapide (sans serveur)

```bash
python3 run.py            # utilise le cache du jour si présent
python3 run.py --force    # force un nouveau fetch d'odds (~2 crédits)
```

Ouvre `bets.html` dans un navigateur.

### Mode serveur local (recommandé)

```bash
python3 serve.py
# → http://localhost:8765
```

Boutons disponibles dans l'interface :
- 🔄 **Rafraîchir les paris du jour** — relance le pipeline (`POST /refresh`).
- 🔄 **Mettre à jour les résultats** (onglet Performance) — fetch `/scores`, calcule auto les gains/pertes 1X2 et Over/Under (`POST /update-results`).
- ✓ / ✗ à côté des picks buteur — marquage manuel (`/scores` ne donne pas les buteurs individuels). Stocké dans `data/manual_outcomes.json` (gitignored).

## Architecture

```
unibet-bets/
├── run.py                  # entrée principale (fetch + render)
├── serve.py                # serveur local + endpoints /refresh /update-results /mark-outcome
├── src/
│   ├── model.py            # Poisson + Dixon-Coles
│   ├── team_codes.py       # mapping noms d'équipes → codes Elo
│   ├── team_form.py        # forme pré-tournoi (écart vs pic Elo 12 mois)
│   ├── tournament_form.py  # forme déroulé tournoi (déduite des résultats)
│   ├── players.py          # top scoreurs hardcodés + parts de buts
│   ├── insights.py         # narratifs qualitatifs auto-générés par match
│   ├── fetch_odds.py       # The Odds API → odds bulk + buteur per-event
│   ├── fetch_results.py    # The Odds API /scores
│   ├── bet_selector.py     # 3 catégories, combos avec caps, compat. checks
│   ├── performance.py      # picks_history + évaluation gain/perte
│   └── render_html.py      # génération HTML (tabs Paris du jour / Performance)
└── data/                   # caches et state runtime (gitignored sauf elo_raw.tsv)
```

## Avertissement

Outil personnel à fins d'analyse. Le pari sportif comporte des risques de perte financière. Les EV affichées sont des espérances mathématiques sur de nombreuses répétitions, pas des garanties.
