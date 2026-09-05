"""
Contrat — un secret peut être fourni SANS passer par les valeurs Helm.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.6

Pourquoi
--------
Helm **conserve les valeurs d'une release dans le cluster**, dans un secret
`sh.helm.release.v1.<release>.vN`. Tout ce qu'on passe par `--set` y reste, et
`helm get values <release>` le rend en clair.

Vérifié le 29/08/2026 sur la release en cours : son `matrix.botPassword` se relit ainsi.
Ce n'est pas une hypothèse, c'est un constat.

Conséquences, toutes les trois réelles :

- le secret survit à la release, dans les **révisions précédentes** conservées ;
- il est lisible par **quiconque a accès au namespace**, sans passer par le secret
  applicatif ;
- il apparaît dans les journaux de qui exécute `helm get values` pour diagnostiquer.

`llm.existingSecret` coupe court : l'opérateur crée le secret lui-même, Helm ne le voit
jamais, et le déploiement n'a plus besoin de le manipuler.

    kubectl create secret generic ma-cle --from-literal=LLM_API_KEY=...
    helm install colaig-test ... --set llm.existingSecret=ma-cle

Ce n'est pas une commodité : c'est ce qui permet à quelqu'un de déployer sans confier
sa clé à l'outil, ni à qui l'exécute.
"""
from __future__ import annotations

import json
import pathlib

SECRET = ((pathlib.Path(__file__).resolve().parent.parent / "deploy" / "helm" / "colaig"
           / "templates" / "secret.yaml").read_text(encoding="utf-8"))
DEPLOIEMENT = ((pathlib.Path(__file__).resolve().parent.parent / "deploy" / "helm"
                / "colaig" / "templates" / "deployment.yaml").read_text(encoding="utf-8"))
SCHEMA = json.loads((pathlib.Path(__file__).resolve().parent.parent / "deploy" / "helm"
                     / "colaig" / "values.schema.json").read_text(encoding="utf-8"))


def test_le_champ_existe_dans_le_schema():
    """Sans lui, le formulaire Onyxia n'offre pas l'option et personne ne la découvre."""
    champ = SCHEMA["properties"]["llm"]["properties"].get("existingSecret")
    assert champ is not None, "`llm.existingSecret` absent du schema"
    assert champ.get("default") == ""


def test_le_chart_NE_CREE_PAS_de_secret_quand_un_secret_externe_est_fourni():
    """Deux secrets pour la même clé, c'est un jour où l'on corrige le mauvais.

    Et le secret du chart porterait une valeur vide, qui écraserait la vraie au montage.
    """
    assert "existingSecret" in SECRET, (
        "le gabarit du secret ignore `existingSecret`"
    )
    assert SECRET.strip().startswith("{{-"), (
        "le gabarit doit etre conditionne des sa premiere ligne"
    )


def test_le_deploiement_MONTE_le_secret_externe_quand_il_existe():
    """Le champ ne sert à rien si le conteneur continue de lire celui du chart."""
    assert "existingSecret" in DEPLOIEMENT, (
        "le deploiement monte toujours le secret du chart, quoi qu'on lui passe"
    )


def test_sans_secret_externe_le_comportement_est_INCHANGE():
    """L'option ne doit pas devenir une obligation.

    Un opérateur qui passe `llm.apiKey` comme avant doit continuer d'être servi — c'est
    ce qui rend l'ajout sans risque pour les déploiements existants.
    """
    assert "colaig.fullname" in SECRET
    assert "$cleLLM" in SECRET
