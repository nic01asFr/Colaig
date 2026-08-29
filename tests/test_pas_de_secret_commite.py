"""
Garde-fou — aucune forme de secret dans les fichiers suivis par git.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0

Le dépôt est **public**. Un secret commité ne se rattrape pas par une suppression :
l'objet git reste, et il a déjà été indexé. Ce test est donc une barrière *avant* le
commit, pas un constat après.

Il ne scanne que les fichiers **suivis par git** (`git ls-files`). C'est volontaire et
c'est la bonne portée : `.env` est ignoré, il contient de vraies clés, et le scanner
n'a pas à les lire — seul ce qui est commité peut fuir.

Origine : une alerte de détection de secret a été levée sur un `placeholder` HTML qui
respectait exactement le format d'un jeton Telegram (9 chiffres, deux-points,
35 caractères). C'était un faux positif — mais une alerte à répétition sur un faux
positif apprend à ignorer les alertes, ce qui est pire que pas d'alerte du tout. Le
placeholder a été rendu hors format, et ce test empêche le retour du problème comme
celui du vrai.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent

# Chaque entrée : (nom lisible, motif, fichiers exemptés)
MOTIFS: list[tuple[str, re.Pattern, set[str]]] = [
    ("jeton de bot Telegram", re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{35}\b"), set()),
    ("clé d'API préfixée sk-", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"), set()),
    ("PAT GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), set()),
    ("PAT GitHub fine-grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"), set()),
    ("jeton Vault", re.compile(r"\bhvs\.[A-Za-z0-9_-]{20,}\b"), set()),
    # Le corps doit ressembler à du base64 réel : un en-tête suivi de « fake » est une
    # fixture, pas une clé. Exempter le fichier ferait un trou ; affiner le motif, non.
    (
        "clé privée",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,20}?[A-Za-z0-9+/=\s]{100,}"),
        set(),
    ),
]

# Ce fichier cite les motifs pour pouvoir les refuser : il s'exclut lui-même.
FICHIERS_EXEMPTES = {"tests/test_pas_de_secret_commite.py"}

EXTENSIONS_BINAIRES = {".faiss", ".pkl", ".png", ".jpg", ".jpeg", ".gif", ".ico",
                       ".pdf", ".zip", ".whl", ".woff", ".woff2", ".ttf"}


def _fichiers_suivis() -> list[str]:
    """Fichiers suivis par git. Liste vide si le dépôt n'est pas un dépôt git."""
    try:
        sortie = subprocess.run(
            ["git", "ls-files"], cwd=RACINE, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
        return []
    if sortie.returncode != 0:  # pragma: no cover
        return []
    return [ligne for ligne in sortie.stdout.splitlines() if ligne.strip()]


@pytest.mark.parametrize("nom,motif,exemptions", MOTIFS, ids=[m[0] for m in MOTIFS])
def test_aucun_secret_dans_les_fichiers_suivis(nom, motif, exemptions):
    suivis = _fichiers_suivis()
    if not suivis:
        pytest.skip("dépôt git indisponible — rien à scanner")

    fautifs: list[str] = []
    for rel in suivis:
        if rel in FICHIERS_EXEMPTES or rel in exemptions:
            continue
        chemin = RACINE / rel
        if chemin.suffix.lower() in EXTENSIONS_BINAIRES or not chemin.is_file():
            continue
        try:
            contenu = chemin.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # Recherche sur le **contenu entier**, pas ligne par ligne : une clé privée
        # PEM s'étale sur plusieurs lignes, et un scan ligne à ligne ne la verrait
        # jamais — le garde-fou passerait au vert en laissant fuir exactement ce
        # qu'il est censé arrêter.
        for trouvaille in motif.finditer(contenu):
            numero = contenu[: trouvaille.start()].count("\n") + 1
            fautifs.append(f"  {rel}:{numero}")

    if fautifs:
        pytest.fail(
            f"{nom} détecté dans des fichiers suivis par git — le dépôt est public :\n"
            + "\n".join(fautifs)
            + "\n\nSi c'est un exemple, le rendre hors format (un placeholder n'a pas "
            "besoin de respecter la forme réelle). Si c'est un vrai secret : le "
            "révoquer, puis purger l'historique — une suppression ne suffit pas."
        )


def test_les_motifs_detectent_vraiment():
    """Un garde-fou qu'on n'a jamais vu se déclencher ne vaut rien.

    Échantillons **fabriqués** pour ce test — aucun n'est un secret réel. Ils prouvent
    que chaque motif mord, et que la recherche porte bien sur le contenu entier : la
    clé PEM ci-dessous s'étale sur plusieurs lignes, et un scan ligne à ligne la
    laisserait passer.
    """
    par_nom = {nom: motif for nom, motif, _ in MOTIFS}

    pem_multiligne = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        + "\n".join(["QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5YWJjZGVm"] * 3)
        + "\n-----END RSA PRIVATE KEY-----\n"
    )

    echantillons = {
        "jeton de bot Telegram": "987654321:" + "A" * 35,
        "clé d'API préfixée sk-": "sk-" + "b" * 40,
        "PAT GitHub": "ghp_" + "c" * 40,
        "PAT GitHub fine-grained": "github_pat_" + "d" * 60,
        "jeton Vault": "hvs." + "e" * 30,
        "clé privée": pem_multiligne,
    }

    assert set(echantillons) == set(par_nom), "un motif n'a pas d'échantillon de preuve"
    for nom, echantillon in echantillons.items():
        assert par_nom[nom].search(echantillon), f"le motif « {nom} » ne détecte rien"

    # Et il ne doit pas mordre sur ce qui n'est pas un secret.
    assert not par_nom["clé privée"].search(
        "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n"
    ), "une fixture évidente ne doit pas déclencher l'alerte"
    assert not par_nom["jeton de bot Telegram"].search("jeton fourni par @BotFather")


def test_env_nest_pas_suivi():
    """`.env` ne doit jamais entrer dans l'index, quelles que soient les circonstances."""
    suivis = _fichiers_suivis()
    if not suivis:
        pytest.skip("dépôt git indisponible")
    fautifs = [f for f in suivis if Path(f).name == ".env" or f.endswith("/.env")]
    assert not fautifs, f".env suivi par git : {fautifs}"


# Les trois endroits ou la mention SUBSISTE legitimement, chacun avec sa raison.
#
# D13 pose « Colaig n'est rattache a aucune organisation dans le depot », au motif qu'un
# lecteur d'un autre ministere y lirait une appartenance qui decourage la reprise. Ce
# motif ne vaut pas partout :
_MENTIONS_ADMISES = {
    # La decision D13 elle-meme : elle NOMME ce qu'elle a fait retirer. L'effacer
    # effacerait la trace de la regle. Meme raison que l'archive `CLAUDE.v3-original.md`,
    # que D13 exempte deja explicitement.
    "_chantier/DECISIONS.md",
    "docs/CLAUDE.v3-original.md",
    # Ce fichier : il porte le mot pour pouvoir le chercher.
    "tests/test_pas_de_secret_commite.py",
    # ATTRIBUTION — en attente d'arbitrage, voir AVANCEMENT du 29/08. Une mention de
    # copyright nomme le titulaire des droits par necessite ; la retirer est un acte
    # juridique, pas un nettoyage. `LICENSE` n'est pas scanne (sans extension), mais
    # porte la meme question.
    "pyproject.toml",
    "README.md",
    "deploy/helm/colaig/Chart.yaml",
}


def test_aucune_organisation_nommee_dans_le_depot():
    """Consigne du chantier : rien de nominatif, et pas de mention d'organisation.

    Elle apparaissait dans trois fichiers suivis — deux fois comme registre d'image
    par défaut, une fois comme adresse de contact. Un défaut de registre nommant une
    organisation fait deux choses : il inscrit une appartenance que le dépôt ne doit pas
    porter, et il produit un `ImagePullBackOff` chez quiconque déploie sans le changer.

    Le défaut est désormais VIDE plutôt que deviné : un champ obligatoire non renseigné
    se lit ; une organisation plausible ne se lit pas.
    """
    racine = Path(__file__).resolve().parent.parent
    suivis = subprocess.run(["git", "ls-files"], cwd=racine, capture_output=True,
                            text=True, check=True).stdout.split()

    fautifs = []
    for nom in suivis:
        chemin = racine / nom
        if chemin.suffix.lower() not in (".py", ".md", ".yaml", ".yml", ".json", ".toml"):
            continue
        if nom.replace("\\", "/") in _MENTIONS_ADMISES:
            continue
        if "_chantier/mesures" in nom.replace("\\", "/"):
            continue
        try:
            texte = chemin.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "cerema" in texte.lower():
            fautifs.append(nom)

    assert fautifs == [], (
        f"mention d'organisation dans : {fautifs} — voir D13. Si la mention est une "
        "ATTRIBUTION (copyright, auteurs, mainteneurs), elle releve d'un arbitrage : "
        "l'ajouter a `_MENTIONS_ADMISES` avec sa raison, pas la retirer en silence."
    )
