"""
Colaig — un nom de fichier n'est pas une URL.

LA PROPRIÉTÉ QUE CES TESTS ÉTABLISSENT
----------------------------------------
Pour tout nom de fichier, **le chemin rendu par un listing doit désigner le même objet
quand on le redemande.** C'est un aller-retour, et il doit être l'identité.

Le backend WebDAV le cassait sur une moitié du trajet :

- **à la lecture**, `_parse_propfind` applique `unquote()` sur le `href` — le chemin
  logique est donc correct : `note %232.pdf` devient `note #2.pdf` ;
- **à l'écriture**, `_url()` recollait ce chemin tel quel dans l'URL :

      return f"{self._base_url}/{path.lstrip('/')}"

Le `#` rouvre alors un **fragment d'URL**, et le serveur ne reçoit que ce qui le
précède. Le fichier est listé, puis introuvable. Idem pour `?`, qui ouvre une chaîne de
requête, et pour `%`, qui amorce une séquence d'échappement.

CE QUI DISTINGUE CES CARACTÈRES DES ACCENTS
---------------------------------------------
Les accents et les espaces sont une affaire d'**octets** : le client HTTP les encode
lui-même, et rien ne se casse. `#`, `?` et `%` sont une affaire de **structure** : ils
changent la grammaire de l'URL, donc ce que le serveur croit qu'on lui demande.

C'est pour cela qu'un tel défaut se voit rarement en test et toujours en production :
il faut un vrai nom de fichier, écrit par un humain.

CE QUI N'EST PAS CONCERNÉ
---------------------------
`s3.py` passe la clé à boto3, qui l'encode. `local.py` ouvre un fichier, sans URL. Le
défaut appartient au seul backend qui construit ses URL à la main.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest

from colaig.integrations.storage.webdav import WebDAVStorage

# Des noms qu'un agent écrit réellement. Les trois premiers cassent la STRUCTURE de
# l'URL ; les suivants n'en changent que les octets.
NOMS = [
    "note #2 réunion.pdf",
    "questions ? réponses.md",
    "taux 100%.pdf",
    "réation accident-situation d'urgence.pdf",
    "fiche réflexe agression.pdf",
    "accompagnement décès/guide.pdf",
    "rapport [final].docx",
    "budget&prévisions.xlsx",
]


@pytest.fixture
def backend():
    return WebDAVStorage(base_url="https://exemple.invalid/dav/colaig",
                         username="u", password="p")


@pytest.mark.parametrize("nom", NOMS)
def test_l_aller_retour_est_l_identite(backend, nom):
    """LA propriété. Ce qu'un listing rend doit redésigner le même objet.

    On reproduit le trajet complet : le chemin est encodé pour la requête, et le
    serveur — comme `_parse_propfind` — le décode. On doit retrouver l'original.
    """
    url = backend._url(f"/dossier/{nom}")
    chemin_transporte = url[len("https://exemple.invalid/dav/colaig/"):]

    assert unquote(chemin_transporte) == f"dossier/{nom}", (
        f"aller-retour rompu pour « {nom} » : le serveur recevra autre chose"
    )


@pytest.mark.parametrize("caractere", ["#", "?", "%"])
def test_les_caracteres_de_structure_sont_echappes(backend, caractere):
    """Ceux-là ne changent pas les octets, ils changent la grammaire de l'URL.

    Un `#` non encodé ouvre un fragment : le serveur ne reçoit que ce qui précède, et
    le fichier devient introuvable après avoir été listé.

    LA FORMULATION JUSTE. « le caractère est absent » serait faux pour `%` : encodé,
    il devient `%25`, qui en contient un. Ce qu'on exige est qu'aucun `%` ne subsiste
    HORS d'une séquence d'échappement valide, et qu'aucun `#` ni `?` ne subsiste du
    tout — ce sont eux qui coupent l'URL.
    """
    transporte = backend._url(
        f"/dossier/fichier{caractere}suite.pdf").split("/dav/colaig/", 1)[1]

    assert "#" not in transporte and "?" not in transporte, (
        f"l'URL reste coupée par un « # » ou un « ? » : {transporte}"
    )
    assert re.fullmatch(r"(?:[^%]|%[0-9A-Fa-f]{2})*", transporte), (
        f"un « % » subsiste hors d'une séquence d'échappement : {transporte}"
    )


def test_les_separateurs_restent_des_separateurs(backend):
    """Encoder les `/` transformerait une arborescence en un seul nom de fichier."""
    url = backend._url("/dossier/sous-dossier/fichier.pdf")

    assert url.endswith("/dossier/sous-dossier/fichier.pdf")
    assert "%2F" not in url


def test_une_url_absolue_passe_intacte(backend):
    """Comportement d'origine : un `href` absolu est déjà encodé par le serveur.

    Le ré-encoder produirait des `%25` à la place de chaque `%`.
    """
    absolue = "https://exemple.invalid/dav/colaig/dossier/note%20%232.pdf"
    assert backend._url(absolue) == absolue


def test_un_chemin_deja_encode_n_est_pas_encode_deux_fois(backend):
    """Le piège symétrique du défaut corrigé.

    Les chemins de Colaig sont **logiques** — produits par `paths.py` ou rendus par un
    listing, donc déjà décodés. Encoder une seconde fois donnerait `%2523` pour un `#`,
    et casserait l'aller-retour dans l'autre sens.
    """
    url = backend._url("/dossier/note #2.pdf")
    assert "%2523" not in url
    assert url.endswith("note%20%232.pdf")


def test_s3_laisse_la_cle_a_boto3():
    """Le contre-exemple qui borne le lot.

    `s3.py` ne construit pas d'URL : il passe la clé à boto3, qui l'encode. Y ajouter un
    encodage produirait un objet dont le nom contient littéralement « %23 ».
    """
    from colaig.integrations.storage.s3 import S3Storage

    s3 = S3Storage(bucket_name="b", access_key="k", secret_key="s")
    assert s3._full_key("/dossier/note #2.pdf") == "dossier/note #2.pdf"
