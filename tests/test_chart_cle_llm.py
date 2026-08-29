"""
Contrat — d'où le chart tire la clé LLM sur Onyxia / SSP Cloud.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.6

Le mécanisme, relevé sur la source
------------------------------------
La configuration publique de l'instance (`datalab.sspcloud.fr/api/public/configuration`,
bloc `regions[0].data.ai`) le dit mot pour mot :

    « Vos identifiants AI Gateway sont injectés de façon sécurisée dans votre
      environnement à chaque démarrage du service. »
    « Votre session OIDC vous donne un accès transparent à la passerelle IA. »

    oauthProvider    : oidc
    clientID         : onyxia-token-exchange-bridge

Les identifiants sont donc **poussés au lancement**, dérivés de la session OIDC. Rien
n'est à découvrir à l'exécution.

Le canal est `x-onyxia.overwriteDefaultWith` dans `values.schema.json` — Onyxia remplit
les valeurs par défaut du formulaire depuis le contexte de l'utilisateur. Les
placeholders sont relevés verbatim sur le chart de référence
`InseeFrLab/helm-charts-interactive-services`, `jupyter-python` :

    {{ai.enabled}}
    {{ai.activeProvider.apiBase}}
    {{ai.activeProvider.apiKey}}
    {{ai.activeProvider.selectedModel}}   (+ overwriteListEnumWith {{ai.activeProvider.models}})

Ce que ce lot a d'abord fait de travers
-----------------------------------------
Une première version explorait les **secrets du namespace** avec le rôle `edit`. Ce
n'est pas le mécanisme de SSPCloud, cela demandait un droit dont le pod n'a pas besoin,
et cela ouvrait une exfiltration à concevoir contre — un secret voisin pris pour une clé
LLM et envoyé à un tiers.

La bonne réponse est **déclarative et tient dans le chart**. Le module Python a été
retiré : il implémentait correctement une mécanique qui n'existe pas.
"""
from __future__ import annotations

import json
import pathlib

import pytest

CHART = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "helm" / "colaig"
SCHEMA = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
SECRET = (CHART / "templates" / "secret.yaml").read_text(encoding="utf-8")


def _x_onyxia(*chemin: str) -> dict:
    noeud = SCHEMA["properties"]
    for i, cle in enumerate(chemin):
        noeud = noeud[cle]
        if i < len(chemin) - 1:
            noeud = noeud["properties"]
    return noeud.get("x-onyxia", {})


# ── Les placeholders, tels qu'Onyxia les remplit ────────────────────────────


@pytest.mark.parametrize("chemin,attendu", [
    (("ai", "enabled"), "{{ai.enabled}}"),
    (("ai", "activeProvider", "apiBase"), "{{ai.activeProvider.apiBase}}"),
    (("ai", "activeProvider", "apiKey"), "{{ai.activeProvider.apiKey}}"),
    (("ai", "activeProvider", "selectedModel"), "{{ai.activeProvider.selectedModel}}"),
])
def test_le_schema_declare_le_placeholder_onyxia(chemin, attendu):
    """Sans `overwriteDefaultWith`, le formulaire de lancement reste vide.

    Onyxia ne devine pas : c'est le schéma du chart qui lui dit où puiser.
    """
    assert _x_onyxia(*chemin).get("overwriteDefaultWith") == attendu


def test_la_liste_des_modeles_vient_aussi_du_contexte():
    """Le modèle est un choix parmi ceux que la passerelle expose.

    Sans `overwriteListEnumWith`, l'utilisateur devrait taper un nom de modèle à la
    main — et se tromper.
    """
    assert _x_onyxia("ai", "activeProvider", "selectedModel").get(
        "overwriteListEnumWith") == "{{ai.activeProvider.models}}"


def test_la_cle_est_marquee_comme_un_SECRET():
    """`x-security: password` masque le champ dans le formulaire Onyxia.

    Une clé affichée en clair dans un formulaire de lancement finit dans une capture
    d'écran, puis dans un ticket.
    """
    champ = SCHEMA["properties"]["ai"]["properties"]["activeProvider"]["properties"]["apiKey"]
    assert champ.get("x-security") == "password"


# ── L'ordre de priorité ─────────────────────────────────────────────────────


def test_une_cle_EXPLICITE_prime_sur_la_passerelle():
    """L'opérateur qui pose `llm.apiKey` a décidé.

    La passerelle est un repli, jamais un remplaçant : sans cet ordre, un déploiement
    hors Onyxia se verrait imposer une valeur vide.
    """
    # ON LIT LA LIGNE D'AFFECTATION, PAS LE FICHIER. Une premiere version comparait
    # des positions dans tout le texte — elle mesurait l'ordre des COMMENTAIRES, qui
    # nomment la passerelle avant la ligne de code.
    ligne = next(l for l in SECRET.splitlines() if "$cleLLM :=" in l)
    assert ligne.index(".Values.llm.apiKey") < ligne.index("activeProvider.apiKey"), (
        f"la cle explicite ne vient pas en premier : {ligne.strip()}"
    )
    assert "| default" in ligne


def test_les_DEUX_variables_recoivent_la_meme_cle():
    """`LLM_API_KEY` et `ALBERT_API_KEY` doivent rester d'accord.

    Elles l'étaient déjà ; les faire diverger au moment d'ajouter un repli produirait
    un pod qui répond selon le backend choisi, ce qui est le pire des cas — il marche
    un jour sur deux.
    """
    # Les USAGES, pas l'affectation : `$cleLLM` apparait trois fois au total.
    assert SECRET.count("{{ $cleLLM | quote }}") == 2


# ── Ce que le chart ne doit PAS faire ───────────────────────────────────────


def test_aucun_module_python_ne_cherche_la_cle_a_l_execution():
    """La découverte est déclarative, au lancement — pas à l'exécution.

    Une première version de ce lot explorait les secrets du namespace avec le rôle
    `edit`. Ce n'est pas le mécanisme de SSPCloud, cela demandait un droit inutile, et
    cela ouvrait une exfiltration : un secret voisin pris pour une clé LLM et envoyé à
    un tiers.

    Si ce test échoue, quelqu'un a réintroduit cette mécanique.
    """
    racine = pathlib.Path(__file__).resolve().parent.parent
    assert not (racine / "colaig" / "integrations" / "sspcloud.py").exists()

    main = (racine / "colaig" / "main.py").read_text(encoding="utf-8")
    assert "decouvrir_cle" not in main
