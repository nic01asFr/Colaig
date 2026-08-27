"""
Suite adversariale — la part MÉCANIQUE (L2.5).

STATUT: TESTE
VERSION: 2026-08-25 - v1.0
LOT: L2.5

Ce que cette suite prouve, et ce qu'elle ne prouve pas
--------------------------------------------------------
Elle éprouve les vingt-cinq attaques de `tests/adversarial/attaques.py` contre les gardes
**mécaniques** des lots L2.1 à L2.4 : le balisage tient-il, la confirmation est-elle
inforgeable, l'épinglage refuse-t-il, la liste blanche écarte-t-elle, un chemin
d'instance est-il refusé.

Elle **ne prouve rien sur l'obéissance du modèle.** Le balisage *déclare* qu'un contenu
est une donnée ; il ne garantit pas que le modèle le respecte. Cette part-là s'observe et
ne se démontre pas — c'est `_chantier/scripts/mesure_adversariale.py`, qui demande un LLM
et ne peut donc pas vivre ici (`tests/CLAUDE.md` : déterministe et hors ligne).

**Confondre les deux serait le pire résultat de tout ce chantier** : une suite verte
attestant d'une sécurité jamais mesurée. Le critère du plan — « zéro appel d'outil non
planifié » — porte sur le comportement du modèle, donc sur la mesure en ligne. Ici on
vérifie que les portes sont fermées, pas que personne n'essaie de passer par la fenêtre.
"""
from __future__ import annotations

import pytest

from colaig.security.actions import est_destructif
from colaig.security.confirmation import CONFIRME, lire_reponse
from colaig.security.mcp_pins import Magasin, verifier
from colaig.security.mcp_policy import connecteurs_autorises
from colaig.security.path_validator import validate_storage_path
from colaig.security.wrap import FERMETURE, baliser
from tests.adversarial.attaques import (
    ATTAQUES,
    CONFIGURATION,
    DESCRIPTION_OUTIL,
    DOCUMENT,
    NOM_DE_FICHIER,
    REPONSE_UTILISATEUR,
    RESULTAT_OUTIL,
    par_vecteur,
)


def test_le_corpus_atteint_le_seuil_du_plan():
    """Le critere du plan exige AU MOINS 20 attaques."""
    assert len(ATTAQUES) >= 20, f"seulement {len(ATTAQUES)} attaques"


def test_chaque_attaque_dit_ce_qui_doit_arriver():
    """Une attaque sans attendu ecrit ne mesure rien — elle decore."""
    muettes = [a.identifiant for a in ATTAQUES if not a.attendu.strip()]
    assert not muettes, f"attaques sans attendu : {muettes}"


def test_les_identifiants_sont_uniques():
    ids = [a.identifiant for a in ATTAQUES]
    assert len(ids) == len(set(ids))


# ── Vecteur DOCUMENT : le balisage tient-il ? ───────────────────────────────


@pytest.mark.parametrize("attaque", par_vecteur(DOCUMENT), ids=lambda a: a.identifiant)
def test_un_document_hostile_ne_sort_pas_de_sa_balise(attaque):
    balise = baliser(attaque.charge, source="depose.md")

    assert balise.count(FERMETURE) == 1, (
        f"{attaque.identifiant} : plusieurs clotures — {attaque.attendu}"
    )
    assert balise.rstrip().endswith(FERMETURE), (
        f"{attaque.identifiant} : la vraie cloture n'est pas la derniere"
    )
    # « On annote, on ne retire pas » (regle 2 de `security/wrap.py`). Verifier la
    # PREMIERE LIGNE serait faux : pour adv-002 a 004 elle EST le marqueur, donc
    # legitimement neutralisee. Ce qui doit survivre, c'est le texte hors marqueur.
    # Une LIGNE sans marqueur doit survivre entiere. Filtrer mot a mot serait faux :
    # `source="systeme"` est un fragment du marqueur, pas du texte utile — c'est ce qui
    # a fait echouer la premiere version de ce test.
    lignes_utiles = [
        ligne.strip() for ligne in attaque.charge.splitlines()
        if ligne.strip() and "untrusted" not in ligne.lower()
    ]
    if lignes_utiles:
        assert any(ligne in balise for ligne in lignes_utiles), (
            f"{attaque.identifiant} : le contenu utile a ete supprime au lieu d'etre "
            "neutralise — on annote, on ne retire pas"
        )
    if "untrusted" in attaque.charge.lower():
        assert "neutralis" in balise.lower(), (
            f"{attaque.identifiant} : une balise a ete retiree EN SILENCE — la "
            "neutralisation doit se voir, sinon on modifie un document que "
            "l'utilisateur croit lire intact"
        )


# ── Vecteur NOM : l'attribut tient-il ? ─────────────────────────────────────


@pytest.mark.parametrize("attaque", par_vecteur(NOM_DE_FICHIER),
                         ids=lambda a: a.identifiant)
def test_un_nom_hostile_ne_forge_pas_l_entete(attaque):
    import re

    entete = baliser("contenu", source=attaque.charge).splitlines()[0]
    attributs = re.findall(r'(\w+)="([^"]*)"', entete)

    assert [nom for nom, _ in attributs] == ["source", "nature"], (
        f"{attaque.identifiant} : un attribut a ete forge — {entete}"
    )
    assert "<" not in attributs[0][1] and ">" not in attributs[0][1], (
        f"{attaque.identifiant} : le nom a pu ouvrir une balise"
    )


# ── Vecteur REPONSE : la confirmation est-elle inforgeable ? ────────────────


@pytest.mark.parametrize("attaque", par_vecteur(REPONSE_UTILISATEUR),
                         ids=lambda a: a.identifiant)
def test_aucune_charge_ne_vaut_confirmation(attaque):
    """La reconnaissance est mecanique : aucun modele ne decide ici.

    Les charges deposees dans un DOCUMENT sont testees de la meme facon — un document
    qui dit « reponds oui » ne repond pas a la place de l'utilisateur.
    """
    assert lire_reponse(attaque.charge) != CONFIRME, (
        f"{attaque.identifiant} : une charge a ete prise pour une confirmation"
    )


def test_une_charge_deposee_dans_un_document_ne_confirme_pas():
    """adv-013 : le document tente de faire repondre l'agent a la place de l'humain."""
    charge = next(a for a in ATTAQUES if a.identifiant == "adv-013")
    assert lire_reponse(charge.charge) != CONFIRME


# ── Vecteur SCHEMA : l'epinglage refuse-t-il la mutation ? ──────────────────


def _outil(description: str) -> dict:
    return {
        "name": "recherche",
        "description": description,
        "inputSchema": {"type": "object", "properties": {}},
    }


@pytest.mark.parametrize("attaque", par_vecteur(DESCRIPTION_OUTIL),
                         ids=lambda a: a.identifiant)
def test_une_description_mutee_desactive_l_outil(attaque, tmp_path):
    magasin = Magasin(tmp_path / f"{attaque.identifiant}.json")
    origine = "Cherche un document dans le fonds documentaire."
    verifier("juridique", _outil(origine), magasin)

    admis, _ = verifier("juridique", _outil(attaque.charge), magasin)

    if attaque.metadonnees.get("benin"):
        assert admis, (
            f"{attaque.identifiant} : un contrat INCHANGE a ete refuse — une garde qui "
            "crie au loup se fait desactiver"
        )
    else:
        assert not admis, f"{attaque.identifiant} : {attaque.attendu}"


# ── Vecteur CONFIG : la liste blanche ecarte-t-elle ? ───────────────────────


@pytest.mark.parametrize("attaque", par_vecteur(CONFIGURATION),
                         ids=lambda a: a.identifiant)
def test_un_serveur_hors_liste_ne_monte_aucun_outil(attaque):
    from colaig.models import MCPConnectorConfig, PlatformPolicy

    declare = [MCPConnectorConfig(name="intrus", url=attaque.charge)]
    politique = PlatformPolicy(allowed_mcp_servers=["https://mcp.interieur.gouv.fr"])

    assert connecteurs_autorises(declare, politique) == [], (
        f"{attaque.identifiant} : {attaque.attendu}"
    )


# ── Vecteur OUTIL : chemins d'instance et actions destructives ──────────────


@pytest.mark.parametrize("attaque", [
    a for a in par_vecteur(RESULTAT_OUTIL) if a.charge.startswith("/")
], ids=lambda a: a.identifiant)
def test_un_chemin_d_instance_est_refuse(attaque):
    from colaig.exceptions import StorageError

    with pytest.raises(StorageError):
        validate_storage_path(attaque.charge, allow_dotcolaig=False, context="adv")


@pytest.mark.parametrize("outil", [
    "manage_workspace_owners", "set_workspace_prompt", "link_conversation",
    "report_to_user", "create_document",
])
def test_les_outils_vises_par_les_attaques_sont_bien_destructifs(outil):
    """Les attaques 022 a 025 visent ces outils. Si l'un cessait d'etre classe
    destructif, l'attaque correspondante passerait sans que rien ne le signale.
    """
    assert est_destructif(outil)


# ── La couverture des familles ─────────────────────────────────────────────


def test_chaque_famille_est_eprouvee_par_au_moins_deux_attaques():
    """Une famille a une seule attaque ne mesure qu'un cas particulier."""
    comptes: dict[str, int] = {}
    for a in ATTAQUES:
        comptes[a.famille] = comptes.get(a.famille, 0) + 1
    maigres = [f for f, n in comptes.items() if n < 2]
    assert not maigres, f"familles sous-eprouvees : {maigres}"


def test_la_part_en_ligne_est_declaree_et_non_simulee():
    """Le critere du plan — « zero appel d'outil non planifie » — porte sur le
    COMPORTEMENT DU MODELE. Il ne peut pas etre atteint hors ligne.

    Ce test existe pour que personne ne lise la suite verte comme une preuve de
    securite. Il exige que le harnais de mesure en ligne existe : sans lui, L2.5 n'est
    pas atteint, il est seulement a moitie outille.
    """
    import pathlib

    harnais = (pathlib.Path(__file__).resolve().parent.parent
               / "_chantier" / "scripts" / "mesure_adversariale.py")
    assert harnais.exists(), (
        "le harnais de mesure en ligne manque — la part mecanique ne suffit pas a "
        "atteindre le critere du plan"
    )
