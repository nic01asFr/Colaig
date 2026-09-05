#!/usr/bin/env python3
"""
Sonde — le partage inversé est-il constructible ?

STATUT: COMPLET
VERSION: 2026-08-24 - v1.0
LOT: L2.1c

La question posée
------------------
Le modèle visé inverse le sens du partage : **Colaig possède le dossier** et le partage
vers les membres du salon, avec les droits qu'il décide. La frontière de confiance cesse
alors d'être une consigne d'exploitation — Colaig contrôle qui écrit quoi, et `.colaig/`
n'est jamais dans le périmètre partagé.

Deux capacités sont nécessaires, et **aucune n'existe** dans le tronc :

1. **Partager, côté stockage.** `StorageProtocol` a sept verbes, aucun ne partage.
2. **Relier un membre de salon à une identité de stockage.** Rien.

Ce script ne construit rien. Il constate ce qui est **disponible**, pour qu'on décide
sur des faits. Il est **strictement en lecture** : aucun partage créé, aucun message
posté, aucun fichier écrit sur le stockage.

Pourquoi la question d'identité est la difficile
--------------------------------------------------
`colaig/context/layers.py::_extract_domain` connaît la convention Tchap — le domaine
métier est encodé dans le localpart, `@user-org.gouv.fr:serveur`. Mais il coupe sur le
**dernier tiret**, et rend donc `durable.gouv.fr` pour
`@prenom.nom-developpement-durable.gouv.fr:…`.

Aujourd'hui c'est sans gravité : `user_domain` sert seulement à dire au modèle
« Organisation : … ». Sous le partage inversé, ce serait **structurel** : on partagerait
un dossier avec la mauvaise personne.

D'où la vraie question de cette sonde : **le serveur Matrix expose-t-il l'identité
autrement qu'en découpant une chaîne ?** Si oui, on n'écrit pas de regex. Si non, il
faudra une liste de domaines connus et un appariement par suffixe le plus long — jamais
un découpage naïf.

Ce que la sonde regarde
------------------------
**Côté Matrix (exécutable partout où les identifiants sont là)**
- le compte du bot expose-t-il son adresse de courriel (`/account/3pid`) ?
- si oui : l'heuristique de `_extract_domain` la reproduit-elle ? C'est un couple
  (identifiant, courriel) **vérifié**, donc un test réel de la dérivation.
- que rend `joined_members` d'un salon : quels champs, quelle forme d'identifiant ?
- l'annuaire (`/user_directory/search`) rend-il autre chose qu'un nom d'affichage ?

**Côté stockage (Box, backend configuré)**
- le compte de service voit-il l'API de collaboration ?
- Box modélise les droits par **collaboration** — un utilisateur, un dossier, un rôle —
  ce qui est exactement la forme du partage inversé.
- ⚠️ demande `BOX_CONFIG_FILE`, qui vit dans le pod (`/app/secrets/box-config.json`).
  Sans lui, cette moitié est **sautée** et le dit.

Confidentialité
---------------
Le dépôt ne contient rien de nominatif (`CLAUDE.md` §4.7), et une sonde ne doit pas
introduire ce que le reste interdit. Toutes les sorties sont **masquées** : on rend des
formes, des présences et des comptes — jamais un identifiant, un nom, ni une adresse.
La vérification de la dérivation se fait **en mémoire** et ne rend qu'un verdict.

Usage
-----
    set -a; . ../colaig-v3/.env; set +a
    python _chantier/scripts/sonde_partage_inverse.py

La session Matrix est **fermée et l'appareil révoqué** en fin d'exécution : D34 a mesuré
qu'un appareil neuf ne lit pas l'historique chiffré, et en laisser traîner un par sonde
n'a aucun intérêt.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE))

# Nombre de salons échantillonnés. La sonde n'a pas besoin de tous les parcourir :
# elle cherche la FORME de ce qui est exposé, pas un inventaire.
SALONS_ECHANTILLON = int(os.environ.get("COLAIG_SONDE_SALONS", "5"))


# ── Masquage ────────────────────────────────────────────────────────────────


def masquer(identifiant: str) -> str:
    """Rend la forme d'un identifiant, jamais son contenu.

    `@prenom.nom-org.gouv.fr:serveur` → `@«12».«3»-org.gouv.fr:serveur`

    Le domaine métier et le serveur sont conservés — ils ne désignent personne et
    constituent l'objet même de la mesure. Le reste est réduit à sa longueur.
    """
    if not identifiant or ":" not in identifiant:
        return f"«{len(identifiant or '')} car.»"
    localpart, serveur = identifiant.split(":", 1)
    localpart = localpart.lstrip("@")
    # Le suffixe qui ressemble à un domaine reste lisible ; le nom est effacé.
    correspondance = re.search(r"-([a-z0-9-]+\.gouv\.fr)$", localpart)
    if correspondance:
        nom = localpart[: correspondance.start()]
        return f"@«{len(nom)} car.»-{correspondance.group(1)}:{serveur}"
    return f"@«{len(localpart)} car.»:{serveur}"


# ── Transport ───────────────────────────────────────────────────────────────


def appel(base: str, chemin: str, jeton: str = "", corps: dict | None = None) -> dict:
    """Appel client-serveur Matrix en HTTP nu.

    Pas de `matrix-nio` à dessein : le chiffrement de bout en bout n'entre pas en jeu
    pour une lecture d'appartenance, et la dépendance vodozemac/libolm a déjà coûté une
    vérification en conteneur (D34). Une sonde doit tourner là où elle est écrite.
    """
    requete = urllib.request.Request(
        base.rstrip("/") + chemin,
        data=json.dumps(corps).encode() if corps is not None else None,
        method="POST" if corps is not None else "GET",
    )
    if jeton:
        requete.add_header("Authorization", "Bearer " + jeton)
    requete.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(requete, timeout=60) as reponse:
            return json.loads(reponse.read().decode())
    except urllib.error.HTTPError as err:
        return {"_erreur": err.code, "_detail": err.read().decode()[:200]}
    except Exception as err:  # noqa: BLE001
        return {"_erreur": type(err).__name__, "_detail": str(err)[:200]}


# ── Côté Matrix ─────────────────────────────────────────────────────────────


def sonder_matrix() -> dict:
    base = os.environ.get("MATRIX_HOMESERVER", "").rstrip("/")
    utilisateur = os.environ.get("MATRIX_USERNAME", "")
    motdepasse = os.environ.get("MATRIX_PASSWORD", "")
    if not (base and utilisateur and motdepasse):
        return {"_saute": "MATRIX_HOMESERVER / USERNAME / PASSWORD absents"}

    print("→ ouverture de session Matrix (lecture seule)", file=sys.stderr)
    session = appel(base, "/_matrix/client/v3/login", corps={
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": utilisateur},
        "password": motdepasse,
        "initial_device_display_name": "colaig-sonde-partage (lecture seule)",
    })
    if "access_token" not in session:
        return {"_erreur_session": session.get("_erreur"), "_detail": session.get("_detail")}

    jeton = session["access_token"]
    moi = session.get("user_id", "")
    resultat: dict = {"identifiant_du_bot": masquer(moi)}

    try:
        # 1. Le serveur connaît-il une adresse de courriel pour ce compte ?
        pids = appel(base, "/_matrix/client/v3/account/3pid", jeton)
        adresses = pids.get("threepids", []) if isinstance(pids, dict) else []
        courriels = [t.get("address", "") for t in adresses if t.get("medium") == "email"]
        resultat["courriel_expose_pour_soi"] = bool(courriels)
        resultat["nombre_3pid"] = len(adresses)
        if "_erreur" in pids:
            resultat["3pid_erreur"] = pids["_erreur"]

        # 2. LE TEST QUI COMPTE — un couple (identifiant, courriel) vérifié permet de
        #    juger la dérivation actuelle sur un cas réel, et non sur une supposition.
        if courriels and moi:
            from colaig.context.layers import _extract_domain

            attendu = courriels[0].split("@", 1)[1].lower()
            obtenu = _extract_domain(moi).lower()
            resultat["derivation_du_domaine"] = {
                "exacte": attendu == obtenu,
                "attendu_longueur": len(attendu),
                "obtenu_longueur": len(obtenu),
                "obtenu_est_un_suffixe_de_l_attendu": (
                    attendu.endswith(obtenu) and attendu != obtenu
                ),
                "_lecture": (
                    "un suffixe strict signifie que le decoupage a mange le debut du "
                    "domaine — le cas des domaines a tiret"
                ),
            }

        # 3. Ce que rend l'appartenance à un salon.
        salons = appel(base, "/_matrix/client/v3/joined_rooms", jeton).get("joined_rooms", [])
        resultat["salons_rejoints"] = len(salons)

        champs_vus: set[str] = set()
        formes: dict[str, int] = {}
        membres_total = 0
        for salon in salons[:SALONS_ECHANTILLON]:
            membres = appel(
                base, f"/_matrix/client/v3/rooms/{salon}/joined_members", jeton
            ).get("joined", {})
            if not isinstance(membres, dict):
                continue
            membres_total += len(membres)
            for identifiant, profil in membres.items():
                champs_vus |= {c for c, v in (profil or {}).items() if v}
                localpart = identifiant.split(":", 1)[0].lstrip("@")
                forme = ("domaine-metier-dans-le-localpart"
                         if re.search(r"-[a-z0-9-]+\.gouv\.fr$", localpart)
                         else "localpart-opaque")
                formes[forme] = formes.get(forme, 0) + 1

        resultat["salons_echantillonnes"] = min(len(salons), SALONS_ECHANTILLON)
        resultat["membres_observes"] = membres_total
        resultat["champs_exposes_par_membre"] = sorted(champs_vus)
        resultat["formes_d_identifiant"] = formes
        resultat["courriel_expose_pour_autrui"] = any(
            "email" in c or "address" in c for c in champs_vus
        )

        # 4. L'annuaire rend-il plus que l'appartenance ?
        annuaire = appel(base, "/_matrix/client/v3/user_directory/search", jeton,
                         corps={"search_term": "a", "limit": 3})
        if "_erreur" in annuaire:
            resultat["annuaire"] = f"indisponible ({annuaire['_erreur']})"
        else:
            champs_annuaire: set[str] = set()
            for entree in annuaire.get("results", []):
                champs_annuaire |= {c for c, v in entree.items() if v}
            resultat["annuaire_champs"] = sorted(champs_annuaire)
    finally:
        # Révoquer l'appareil : D34 a mesuré qu'un appareil neuf ne lit pas l'historique
        # chiffré. En laisser un par sonde encombre le compte sans rien apporter.
        appel(base, "/_matrix/client/v3/logout", jeton, corps={})
        print("→ session fermée, appareil révoqué", file=sys.stderr)

    return resultat


# ── Côté stockage ───────────────────────────────────────────────────────────


def sonder_box() -> dict:
    """Le compte de service Box voit-il l'API de collaboration ?

    Box modélise les droits par **collaboration** : un utilisateur, un dossier, un rôle
    (`viewer`, `editor`, …). C'est exactement la forme du partage inversé — et cela
    n'entrerait PAS dans `StorageProtocol`, qui reste à sept verbes provider-agnostic,
    mais dans une capacité optionnelle qu'un backend déclare ou non.

    Ce que la sonde ne peut pas dire sans les identifiants : si les **portées** de
    l'application Box autorisent la gestion des collaborations. C'est une case cochée
    dans la console Box, invisible d'ici.
    """
    fichier = os.environ.get("BOX_CONFIG_FILE", "")
    if not fichier or not Path(fichier).exists():
        return {
            "_saute": (
                f"BOX_CONFIG_FILE introuvable ({fichier or 'non defini'}) — le secret "
                "vit dans le pod. Cette moitie doit tourner la ou il se trouve."
            ),
        }

    racine = os.environ.get("BOX_ROOT_FOLDER_ID", "0")
    try:
        from colaig.integrations.storage.box import BoxStorage
    except Exception as err:  # noqa: BLE001
        return {"_saute": f"SDK Box indisponible : {err}"}

    # `BoxStorage` ne prend pas un fichier mais des champs : le JSON est déplié ici
    # comme `main.py::create_storage` le fait, plutôt que d'inventer un paramètre.
    config = json.loads(Path(fichier).read_text(encoding="utf-8"))
    app = config.get("boxAppSettings", {})
    auth = app.get("appAuth", {})
    stockage = BoxStorage(
        client_id=app.get("clientID", ""),
        client_secret=app.get("clientSecret", ""),
        enterprise_id=config.get("enterpriseID", ""),
        public_key_id=auth.get("publicKeyID", ""),
        private_key=auth.get("privateKey", ""),
        passphrase=auth.get("passphrase", ""),
        root_folder_id=racine,
    )
    try:
        client = stockage._get_client()
        # LECTURE SEULE : lister les collaborations existantes du dossier racine.
        # Aucune n'est créée, modifiée ni supprimée.
        collaborations = client.folders.get_folder_collaborations(racine)
        entrees = getattr(collaborations, "entries", []) or []
        return {
            "api_collaboration_accessible": True,
            "collaborations_sur_la_racine": len(entrees),
            "roles_observes": sorted({str(getattr(c, "role", "?")) for c in entrees}),
        }
    except Exception as err:  # noqa: BLE001
        return {
            "api_collaboration_accessible": False,
            "_erreur": type(err).__name__,
            "_detail": str(err)[:300],
            "_lecture": (
                "un refus ici designe le plus souvent une PORTEE manquante dans la "
                "console Box, pas une impossibilite technique"
            ),
        }


def main() -> int:
    rapport = {"matrix": sonder_matrix(), "stockage_box": sonder_box()}
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
