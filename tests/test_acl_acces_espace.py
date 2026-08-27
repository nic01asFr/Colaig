"""
Contrat — qui accède à un espace, et qui peut le déléguer à une sous-tâche.

STATUT: TESTE
VERSION: 2026-08-27 - v1.0
LOT: L2.6

Pourquoi ce fichier
--------------------
`acl.py` était couvert à 64 % : la moitié de ses chemins n'avait jamais été exercée.
Une garde non exercée est une garde dont on ignore si elle sait refuser — et ce module
est le seul endroit du dépôt qui décide **qui voit quoi**.

Ce qui est épinglé ici, et pourquoi ça compte
-----------------------------------------------
`run_subtask` permet à une tâche de porter une question dans un **autre** espace. C'est
la seule primitive du système qui franchit une frontière d'espace, et elle le fait
**sans utilisateur devant l'écran** : une tâche planifiée s'exécute seule, à trois heures
du matin, avec les identifiants de Colaig.

Elle porte donc une double barrière — l'accès de son demandeur, ET une liste blanche
d'espaces fixée à la création. La seconde n'est pas redondante : les droits du demandeur
peuvent s'élargir après coup, alors que la liste, elle, est figée dans la tâche.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from colaig.exceptions import WorkspaceAccessDenied, WorkspaceNotFound
from colaig.security.acl import WorkspaceACL


def _espace(workspace_id="rh", owners=(), user_ids=(), public=False):
    return SimpleNamespace(
        workspace_id=workspace_id, owners=list(owners),
        user_ids=list(user_ids), public=public,
    )


def _tache(user_id="@alice:tchap.fr", autorises=()):
    return SimpleNamespace(user_id=user_id, workspace_ids_allowed=list(autorises))


# ── L'accès ─────────────────────────────────────────────────────────────────


def test_un_espace_public_est_accessible_a_tous():
    """C'est sa raison d'être : l'espace d'accueil répond avant toute identification."""
    assert WorkspaceACL.can_access(_espace(public=True), "", auth_enabled=True)


def test_un_membre_declare_accede():
    assert WorkspaceACL.can_access(
        _espace(user_ids=["@alice:tchap.fr"]), "@alice:tchap.fr", auth_enabled=True,
    )


def test_un_proprietaire_accede():
    assert WorkspaceACL.can_access(
        _espace(owners=["@alice:tchap.fr"]), "@alice:tchap.fr", auth_enabled=True,
    )


def test_un_tiers_n_accede_pas():
    assert not WorkspaceACL.can_access(
        _espace(user_ids=["@alice:tchap.fr"]), "@bob:tchap.fr", auth_enabled=True,
    )


def test_sans_identite_un_espace_non_public_reste_ferme():
    """Un `user_id` vide ne doit pas valoir passe-partout.

    C'est le cas qui arrive vraiment : D39 a mesuré qu'un tiers des identifiants
    observés dans un salon sont opaques, et que le salon n'expose pas l'adresse de ses
    membres. Une identité manquante est donc un état ordinaire, pas une anomalie — elle
    ne peut pas ouvrir l'espace.
    """
    assert not WorkspaceACL.can_access(
        _espace(user_ids=["@alice:tchap.fr"]), "", auth_enabled=True,
    )


def test_un_espace_absent_ferme():
    assert not WorkspaceACL.can_access(None, "@alice:tchap.fr", auth_enabled=True)


def test_assert_can_access_leve_pour_un_tiers():
    with pytest.raises(WorkspaceAccessDenied):
        WorkspaceACL.assert_can_access(
            _espace(user_ids=["@alice:tchap.fr"]), "@bob:tchap.fr", auth_enabled=True,
        )


# ── L'administration ────────────────────────────────────────────────────────


def test_un_administrateur_global_administre():
    assert WorkspaceACL.can_manage_workspace(
        "@admin:tchap.fr", _espace(), admin_user_ids=["@admin:tchap.fr"],
    )


def test_un_proprietaire_administre_son_espace():
    assert WorkspaceACL.can_manage_workspace(
        "@alice:tchap.fr", _espace(owners=["@alice:tchap.fr"]), admin_user_ids=[],
    )


def test_un_membre_simple_n_administre_pas():
    """Lire n'est pas administrer — sinon tout membre reconfigure l'espace des autres."""
    assert not WorkspaceACL.can_manage_workspace(
        "@bob:tchap.fr", _espace(user_ids=["@bob:tchap.fr"]), admin_user_ids=[],
    )


def test_sans_identite_personne_n_administre():
    assert not WorkspaceACL.can_manage_workspace("", _espace(), admin_user_ids=["x"])


# ── Le franchissement d'espace par une tâche ────────────────────────────────


def test_une_sous_tache_vers_un_espace_inconnu_est_refusee():
    with pytest.raises(WorkspaceNotFound):
        WorkspaceACL.validate_task_workspace(
            _tache(), "espace-fantome", all_workspaces=[_espace("rh")],
        )


def test_une_sous_tache_vers_un_espace_ferme_est_refusee():
    """Le demandeur de la tâche n'a pas accès : la tâche non plus.

    Une tâche s'exécute avec les identifiants de Colaig, qui voit tous les espaces
    qu'on lui a partagés. Sans ce contrôle, planifier une tâche suffirait à lire
    l'espace d'un service auquel on n'appartient pas.
    """
    with pytest.raises(WorkspaceAccessDenied):
        WorkspaceACL.validate_task_workspace(
            _tache("@bob:tchap.fr"), "rh",
            all_workspaces=[_espace("rh", user_ids=["@alice:tchap.fr"])],
        )


def test_une_sous_tache_hors_liste_blanche_est_refusee():
    """La seconde barrière, et elle n'est pas redondante.

    Les droits d'un utilisateur peuvent s'élargir après la création de la tâche ; la
    liste, elle, est figée dedans. Elle borne ce que la tâche pourra atteindre plus
    tard, pas seulement ce qu'elle atteint aujourd'hui.
    """
    espace = _espace("finance", user_ids=["@alice:tchap.fr"])
    with pytest.raises(ValueError):
        WorkspaceACL.validate_task_workspace(
            _tache(autorises=["rh"]), "finance", all_workspaces=[espace],
        )


def test_une_sous_tache_conforme_passe():
    espace = _espace("rh", user_ids=["@alice:tchap.fr"])
    WorkspaceACL.validate_task_workspace(
        _tache(autorises=["rh"]), "rh", all_workspaces=[espace],
    )


def test_sans_liste_blanche_seul_l_acces_decide():
    """Comportement documenté, épinglé pour qu'il soit un choix et non une surprise."""
    espace = _espace("rh", user_ids=["@alice:tchap.fr"])
    WorkspaceACL.validate_task_workspace(_tache(), "rh", all_workspaces=[espace])


# ── Le filtrage d'une liste ─────────────────────────────────────────────────


def test_filter_accessible_ne_rend_que_les_espaces_ouverts():
    espaces = [
        _espace("rh", user_ids=["@alice:tchap.fr"]),
        _espace("finance", user_ids=["@bob:tchap.fr"]),
        _espace("accueil", public=True),
    ]
    retenus = WorkspaceACL.filter_accessible(espaces, "@alice:tchap.fr",
                                             auth_enabled=True)
    assert sorted(w.workspace_id for w in retenus) == ["accueil", "rh"]


# ── Une cible de type inconnu ───────────────────────────────────────────────


def test_un_type_de_livraison_inconnu_est_borne_sans_etre_interprete():
    """Ni refus ni confiance : la chaîne est bornée et rendue telle quelle.

    Refuser casserait un type ajouté plus tard ; interpréter appliquerait des règles de
    chemin à quelque chose qui n'en est pas un. Borner la longueur est le seul contrôle
    qui vaille sans connaître la nature de la cible.
    """
    assert WorkspaceACL.validate_delivery_target("webhook", "abc") == "abc"
    with pytest.raises(ValueError):
        WorkspaceACL.validate_delivery_target("webhook", "a" * 600)


# ── Le defaut mesure : le createur exclu de son propre espace ───────────────


@pytest.mark.asyncio
async def test_le_createur_d_un_espace_peut_le_lire(fake_storage):
    """LE defaut, mesure de bout en bout le 27/08/2026.

    `manage_workspace(action="create")` pose `owners=[createur]` et laisse `user_ids`
    vide. `can_access` ne consultait que `user_ids` : le createur pouvait administrer
    son espace et y rattacher une conversation, mais pas le LIRE — et
    `filter_accessible` le lui cachait dans sa propre liste.

    Ce test passe par `create_workspace` plutot que par un objet fabrique a la main :
    le defaut naissait de l'ecart entre ce que la creation ECRIT et ce que le predicat
    LIT. Un espace construit dans le test aurait pu masquer cet ecart.
    """
    from colaig.context.workspace import create_workspace

    ws = await create_workspace(
        fake_storage, storage_path="/espace-rh/", name="RH",
        owners=["@alice:tchap.fr"],
    )

    assert ws.owners == ["@alice:tchap.fr"]
    assert not ws.user_ids, "la creation ne peuple pas user_ids — c'est la que naissait l'ecart"
    assert WorkspaceACL.can_access(ws, "@alice:tchap.fr", auth_enabled=True), (
        "le createur d'un espace doit pouvoir le lire"
    )


@pytest.mark.asyncio
async def test_le_createur_voit_son_espace_dans_sa_propre_liste(fake_storage):
    """La consequence visible : l'espace disparaissait de la liste de son proprietaire."""
    from colaig.context.workspace import create_workspace

    ws = await create_workspace(
        fake_storage, storage_path="/espace-rh/", name="RH",
        owners=["@alice:tchap.fr"],
    )
    retenus = WorkspaceACL.filter_accessible([ws], "@alice:tchap.fr", auth_enabled=True)
    assert [w.workspace_id for w in retenus] == [ws.workspace_id]


def test_etre_proprietaire_d_UN_espace_n_ouvre_pas_les_AUTRES():
    """L'elargissement reste borne a l'espace possede.

    Sans ce test, `owners` pourrait deriver vers un role global — c'est exactement la
    forme d'escalade que `manage_workspace_owners` reserve deja aux admins globaux.
    """
    autre = _espace("finance", user_ids=["@bob:tchap.fr"])
    assert not WorkspaceACL.can_access(autre, "@alice:tchap.fr", auth_enabled=True)
