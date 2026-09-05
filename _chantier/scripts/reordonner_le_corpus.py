"""Remettre les articles du corpus dans l'ordre du Code.

CE QUI ETAIT FAUX
-------------------
`construire_corpus_mp.py` triait les articles comme des CHAINES :

    085-…-modification-du-marche.md
      R2194-1  R2194-10  R2194-2  R2194-3  …

« R2194-10 » se place entre « R2194-1 » et « R2194-2 ». Vingt-deux des quarante-cinq
fichiers d'articles du Code etaient dans ce cas.

Deux consequences. Un lecteur humain est trompe — et la ligne de tete l'etait aussi,
qui annoncait « R2194-1 a R2194-9 » alors que le fichier va jusqu'a R2194-10. Surtout,
l'elargissement aux voisins sert les positions +/-1 : depuis R2194-1 il servait
R2194-10, pas R2194-2. Une fois sur deux il tirait des voisins arbitraires — ce qui
explique qu'il n'ait jamais montre d'effet en trois campagnes.

CE QUE FAIT CE SCRIPT
-----------------------
Il REORDONNE, il ne reecrit pas : le preambule est conserve tel quel, chaque bloc
d'article est deplace sans etre touche, et le script refuse d'ecrire si l'ensemble des
blocs n'est pas rigoureusement le meme avant et apres.

Le corpus local est versionne dans git : il tient lieu de sauvegarde.

    python _chantier/scripts/reordonner_le_corpus.py            # local, a blanc
    python _chantier/scripts/reordonner_le_corpus.py --ecrire   # local
    python _chantier/scripts/reordonner_le_corpus.py --pousser  # local -> S3 via le pod
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
CORPUS = RACINE / "tests" / "golden" / "corpus-marches-publics"
NAMESPACE = "user-nic01asfr"
ESPACE = "/colaig-mesure-marches-publics/"

_NUMERO = re.compile(r"^([LRD])(\d+)-(\d+)(?:-(\d+))?$")
_BORNES = re.compile(r"^\*\*(\d+) articles? en vigueur\*\* — (.+?) à (.+?)\.$", re.M)


def cle_numerique(titre: str):
    m = _NUMERO.match(titre.strip())
    if not m:
        return None
    return (m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))


def reordonner(texte: str) -> tuple[str, bool]:
    """Rend (texte reordonne, a_change). Preserve tout ce qui n'est pas l'ordre."""
    morceaux = texte.split("\n## Article ")
    if len(morceaux) < 3:
        return texte, False
    preambule, blocs = morceaux[0], morceaux[1:]

    titres = [b.split("\n", 1)[0].strip() for b in blocs]
    cles = [cle_numerique(t) for t in titres]
    if any(c is None for c in cles):
        return texte, False          # CCAG, annexes : pas de numerotation d'articles
    if cles == sorted(cles):
        return texte, False

    ordre = sorted(range(len(blocs)), key=lambda i: cles[i])
    blocs_ordonnes = [blocs[i] for i in ordre]
    assert sorted(blocs_ordonnes) == sorted(blocs), "un bloc a ete altere"

    # La ligne de tete annoncait la borne du tri de CHAINES : « R2194-1 a R2194-9 »
    # pour un fichier qui va jusqu'a R2194-10.
    premier = titres[ordre[0]]
    dernier = titres[ordre[-1]]
    preambule = _BORNES.sub(
        lambda m: f"**{m.group(1)} articles en vigueur** — {premier} à {dernier}.",
        preambule)

    return preambule + "\n## Article " + "\n## Article ".join(blocs_ordonnes), True


LECTURE_DES_EMPREINTES = """
import asyncio, hashlib
from colaig.config import load_config
from colaig.main import create_storage
async def m():
    s = create_storage(load_config())
    for f in await s.list_files('{espace}'):
        nom = f.path.rsplit('/', 1)[-1]
        if not nom.endswith('.md'):
            continue
        b = await s.download(f.path)
        print(nom + ' ' + hashlib.sha256(b).hexdigest()[:16])
asyncio.run(m())
"""


def _pod() -> str:
    out = subprocess.run(
        ["kubectl", "get", "pods", "-n", NAMESPACE,
         "-l", "app.kubernetes.io/instance=colaig-test",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _empreintes_de_l_espace(pod: str) -> dict:
    """sha256 tronquee de chaque document de l'espace, lue depuis le pod.

    On pousse ce qui DIFFERE, pas « ce qu'on vient de reordonner » : une fois le
    corpus local corrige, cette derniere liste est vide et le script ne ferait rien,
    en silence.
    """
    script = LECTURE_DES_EMPREINTES.format(espace=ESPACE)
    out = subprocess.run(["kubectl", "exec", "-n", NAMESPACE, pod, "--", "python", "-c", script],
                         capture_output=True, text=True, encoding="utf-8")
    empreintes = {}
    for ligne in out.stdout.splitlines():
        bouts = ligne.strip().split()
        if len(bouts) == 2 and bouts[0].endswith(".md"):
            empreintes[bouts[0]] = bouts[1]
    if not empreintes:
        print("  [!] aucune empreinte lue : " + out.stderr.strip()[-200:], file=sys.stderr)
    return empreintes


ECRITURE = """
import asyncio, sys
from colaig.config import load_config
from colaig.main import create_storage
async def m():
    s = create_storage(load_config())
    await s.upload('{chemin}', sys.stdin.buffer.read())
    print('ECRIT {nom}')
asyncio.run(m())
"""


def _televerser(pod: str, nom: str, contenu: bytes) -> str:
    """Televerse un document dans l'espace.

    LE CONTENU PASSE PAR L'ENTREE STANDARD. Une premiere version l'encodait en
    base64 dans la ligne de commande : Windows la limite a 32 ko, et un document du
    corpus la depasse — l'echec arrive apres cinq fichiers, a moitie du travail.
    """
    script = ECRITURE.format(chemin=ESPACE + nom, nom=nom)
    out = subprocess.run(
        ["kubectl", "exec", "-i", "-n", NAMESPACE, pod, "--", "python", "-c", script],
        input=contenu, capture_output=True)
    sortie = out.stdout.decode("utf-8", "replace").strip()
    return sortie or out.stderr.decode("utf-8", "replace").strip()[-200:]


def main() -> int:
    ecrire = "--ecrire" in sys.argv
    pousser = "--pousser" in sys.argv

    changes: list[tuple[Path, str]] = []
    for f in sorted(CORPUS.glob("*.md")):
        texte = f.read_text(encoding="utf-8")
        nouveau, change = reordonner(texte)
        if change:
            changes.append((f, nouveau))

    print(f"fichiers a reordonner : {len(changes)}")
    for f, _ in changes:
        print(f"  {f.name}")
    if not (ecrire or pousser):
        print("\n(a blanc — relancer avec --ecrire puis --pousser)")
        return 0

    if ecrire:
        for f, nouveau in changes:
            f.write_text(nouveau, encoding="utf-8", newline="\n")
        print(f"\n{len(changes)} fichiers reecrits localement")

    if pousser:
        # On ne pousse pas « ce qu'on vient de reordonner » — une fois le local
        # corrige, cette liste est vide et le script ne ferait rien en silence.
        # On pousse ce qui DIFFERE de l'espace, empreinte contre empreinte.
        pod = _pod()
        print(f"pod : {pod}")
        distantes = _empreintes_de_l_espace(pod)
        pousses = 0
        for f in sorted(CORPUS.glob("*.md")):
            octets = f.read_bytes()
            locale = hashlib.sha256(octets).hexdigest()[:16]
            if distantes.get(f.name) == locale:
                continue
            print("  " + _televerser(pod, f.name, octets))
            pousses += 1
        print(f"{pousses} fichiers pousses, {len(distantes)} presents dans l'espace")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
