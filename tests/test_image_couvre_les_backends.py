"""
Colaig — l'image doit pouvoir instancier les backends que la configuration accepte.

Ce que le déploiement du 29/08/2026 a montré
----------------------------------------------
`STORAGE_BACKEND=s3` a été accepté par `config.py`, `create_storage` a construit un
`S3Storage`, et le premier appel a levé :

    ImportError: boto3 est requis pour S3Storage. Installez-le : pip install boto3

`requirements.txt` — ce que l'image installe — listait `box-sdk-gen` avec la mention
« optionnel — requis si STORAGE_BACKEND=box », mais **pas `boto3`**. S3 était donc le
seul backend déclaré sans sa dépendance.

Le défaut est silencieux là où il compte : la configuration valide, le démarrage
réussit, et l'échec n'apparaît qu'au premier accès au stockage — c'est-à-dire dans la
sonde de disponibilité, un pod qui ne devient jamais prêt.

Pourquoi le test porte sur `requirements.txt` et non sur l'import
-------------------------------------------------------------------
Un test qui ferait `import boto3` passerait sur un poste de développement où la
bibliothèque est installée, et manquerait exactement le cas réel : **l'image**. Le seul
artefact qui décrit ce que l'image contient est `requirements.txt`, donc c'est lui qu'il
faut lire.
"""

from __future__ import annotations

import pathlib
import re

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent

# Backend → distribution requise pour qu'il fonctionne. Un backend absent de cette
# table n'a pas de dépendance tierce (local, webdav et bigfolder n'utilisent que httpx
# et la bibliothèque standard, déjà présents).
DEPENDANCES = {
    "s3": "boto3",
    "box": "box-sdk-gen",
}


def _requirements() -> str:
    contenu = (RACINE / "requirements.txt").read_text(encoding="utf-8")
    # Les lignes commentées ne sont pas installées.
    return "\n".join(l for l in contenu.splitlines() if not l.strip().startswith("#"))


@pytest.mark.parametrize(("backend", "distribution"), sorted(DEPENDANCES.items()))
def test_le_backend_a_sa_dependance_dans_l_image(backend, distribution):
    """Un backend sélectionnable dont l'image n'a pas la bibliothèque échoue à chaud."""
    assert re.search(rf"^{re.escape(distribution)}\b", _requirements(), re.MULTILINE), (
        f"STORAGE_BACKEND={backend} est accepté par config.py, mais {distribution} "
        f"n'est pas dans requirements.txt : le pod démarre et ne devient jamais prêt"
    )


def test_tous_les_backends_a_dependance_sont_couverts():
    """Garde-fou : un nouveau backend tiers ne doit pas échapper à la table.

    Sans cela, la table ci-dessus vieillit en silence et le test ne protège plus que
    les deux cas d'hier.
    """
    # La liste faisant autorité est celle que `config.py` accepte — pas les
    # comparaisons de `main.py`, qui mêlent stockage, messagerie et LLM.
    source = (RACINE / "colaig" / "config.py").read_text(encoding="utf-8")
    ligne = re.search(r'STORAGE_BACKEND inconnu', source)
    assert ligne, "la validation de STORAGE_BACKEND a changé de forme"
    amont = source[:ligne.start()]
    declares = set(re.findall(r'"([a-z0-9]+)"', amont.rsplit("sb not in", 1)[1]))

    # Ceux qui n'ont besoin de rien de tiers : httpx et la bibliothèque standard.
    sans_dependance = {"local", "webdav", "bigfolder", "msgraph", "gdrive"}

    inconnus = declares - sans_dependance - set(DEPENDANCES)
    assert not inconnus, (
        f"backend(s) sans décision de dépendance : {sorted(inconnus)} — ajoutez-les à "
        f"DEPENDANCES, ou à `sans_dependance` après avoir vérifié leurs imports"
    )
