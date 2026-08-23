# Banc des leviers de recherche — arbitrage H2

**103 cas dorés porteurs d'articles attendus.** Métrique : *tous* les articles
attendus remontent dans les passages rendus. Un article manquant ne peut pas être cité.

FAISS, BM25, RRF et MMR existaient tous dans le code sans avoir jamais été mesurés.

| variante | complets | partiels | nuls |
|---|---|---|---|
| dense k=6 | **88** (85 %) | 4 | 11 |
| dense k=10 | **89** (86 %) | 4 | 10 |
| dense k=15 | **95** (92 %) | 3 | 5 |
| dense k=20 | **96** (93 %) | 2 | 5 |
| BM25 k=6 | **66** (64 %) | 3 | 34 |
| RRF dense+BM25 k=6 | **84** (82 %) | 5 | 14 |
| RRF dense+BM25 k=10 | **91** (88 %) | 4 | 8 |
| RRF + MMR k=6 | **76** (74 %) | 5 | 22 |
| dense + MMR k=6 | **88** (85 %) | 4 | 11 |

## Cas encore en échec, par variante

- **dense k=6** — mp-012, mp-015, mp-025, mp-047, mp-051, mp-057, mp-069, mp-070, mp-093, mp-104, mp-116
- **dense k=10** — mp-015, mp-025, mp-047, mp-051, mp-057, mp-069, mp-070, mp-093, mp-104, mp-116
- **dense k=15** — mp-025, mp-047, mp-069, mp-070, mp-116
- **dense k=20** — mp-025, mp-047, mp-069, mp-070, mp-116
- **BM25 k=6** — mp-001, mp-003, mp-005, mp-008, mp-009, mp-010, mp-012, mp-015, mp-018, mp-024, mp-025, mp-026, mp-032, mp-034, mp-036, mp-039, mp-044, mp-045, mp-046, mp-047, mp-048, mp-050, mp-052, mp-057, mp-069, mp-070, mp-074, mp-081, mp-096, mp-100, mp-106, mp-111, mp-116, mp-118
- **RRF dense+BM25 k=6** — mp-009, mp-010, mp-012, mp-025, mp-032, mp-039, mp-047, mp-050, mp-057, mp-069, mp-070, mp-081, mp-106, mp-116
- **RRF dense+BM25 k=10** — mp-012, mp-025, mp-032, mp-047, mp-048, mp-069, mp-070, mp-116
- **RRF + MMR k=6** — mp-001, mp-005, mp-009, mp-012, mp-024, mp-025, mp-026, mp-031, mp-032, mp-039, mp-045, mp-047, mp-048, mp-050, mp-069, mp-070, mp-074, mp-081, mp-096, mp-106, mp-116, mp-118
- **dense + MMR k=6** — mp-012, mp-015, mp-025, mp-047, mp-051, mp-057, mp-069, mp-070, mp-093, mp-104, mp-116
