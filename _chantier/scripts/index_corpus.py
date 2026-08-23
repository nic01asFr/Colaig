"""Index articles -> texte du corpus fige. Sert a ancrer les cas du jeu dore.

Aucun cas ne doit citer un article absent du corpus : le jeu dore mesurerait alors
la memoire du modele et non le systeme. Cet index rend la verification mecanique.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[2] / "tests" / "golden" / "corpus-marches-publics"
# Tout identifiant d en-tete, pas seulement les numeros du code.
#
# Le motif exigeait un prefixe L, R ou D suivi de chiffres — celui du Code de la
# commande publique. Il ne voyait donc AUCUN article du CCAG, dont les en-tetes
# portent « CCAG Travaux 20 ». Un index aveugle a la moitie de son corpus le
# declare simplement absent, sans erreur : exactement le mode de defaillance
# silencieux que ce chantier passe son temps a fermer.
_ART = re.compile(r"^#{1,6}\s*Article\s+([A-Za-z0-9\-. ]+?)\s*$", re.M)


def index() -> dict[str, dict]:
    """Numero d'article normalise -> {texte, fichier, position}."""
    articles: dict[str, dict] = {}
    for f in sorted(CORPUS.glob("*.md")):
        if f.name.startswith("000-"):
            continue
        contenu = f.read_text(encoding="utf-8")
        bornes = [(m.start(), m.end(), m.group(1)) for m in _ART.finditer(contenu)]
        for i, (deb, fin, brut) in enumerate(bornes):
            suivant = bornes[i + 1][0] if i + 1 < len(bornes) else len(contenu)
            num = re.sub(r"[.\s]", "", brut)
            articles[num] = {
                "texte": contenu[fin:suivant].strip(),
                "fichier": f.name,
            }
    return articles


if __name__ == "__main__":
    a = index()
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps({k: v["texte"] for k, v in a.items()}, ensure_ascii=False))
    elif len(sys.argv) > 1:
        for motif in sys.argv[1:]:
            for num in sorted(k for k in a if k.startswith(motif)):
                print(f"### {num}  [{a[num]['fichier'][:3]}]")
                print(a[num]["texte"][:1400].strip())
                print()
    else:
        print(f"{len(a)} articles indexes")
