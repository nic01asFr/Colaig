# Contribuer à Colaig

Merci de votre intérêt ! Colaig est diffusé sous Licence Ouverte 2.0.

## Mise en place

```bash
pip install -e ".[dev]"
pytest -q --ignore=tests/test_live.py   # ~1570 tests
ruff check colaig
```

## Principes à respecter (inviolables)

- **Zéro base de données** : pas de PostgreSQL/Redis/Qdrant comme dépendance de
  Colaig. Toute persistance passe par `StorageProtocol`.
- **Provider-agnostic** : le code métier dépend des Protocols (`protocols.py`),
  jamais d'une implémentation concrète.
- **Souveraineté** : pas de dépendance cloud non-souveraine par défaut.

Voir `CLAUDE.md` (racine) pour les conventions détaillées.

## Style

- Python 3.11+, type hints, `async/await` pour l'I/O.
- `ruff` doit passer (`ruff check colaig`).
- Noms en anglais dans le code, commentaires/docs en français acceptés.
- Pas d'emojis dans le code.

## Pull requests

1. Brancher depuis `main` (`feat/...`, `fix/...`).
2. Ajouter des tests (les modules critiques visent ≥ 80 %).
3. `pytest` + `ruff` verts.
4. Mettre à jour la doc (README / docs / CLAUDE.md du module) si le comportement change.
5. Décrire le « pourquoi » dans la PR.

## Sécurité

Ne pas ouvrir d'issue publique pour une faille — voir [SECURITY.md](SECURITY.md).
