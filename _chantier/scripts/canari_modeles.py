"""
Canari — le modèle mesuré est-il encore le même ?

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0
LOT: L1.6 (porte de régression)

Le trou qu'il bouche
---------------------
Toutes les valeurs de `reference.json` sont mesurées contre **deux modèles distants** :

    génération   qwen3-6-35b-moe   sur SSPCloud
    embeddings   BAAI/bge-m3       sur Albert

Vérifié le 28/08/2026 : l'API rend bien le nom du modèle servi, mais son catalogue
n'expose **ni version, ni date, ni empreinte** — seulement `id` et `owned_by`. Si les
poids ou la configuration de service changent sous le même nom, toutes les valeurs de
référence deviennent caduques **en silence**, et la porte de régression imputerait la
dérive à notre code.

Ce n'est pas théorique : la soirée du 27/08 a été passée à distinguer à la main une
dérive d'endpoint d'une régression de code, sur une porte devenue rouge sans qu'aucune
ligne du chemin de génération n'ait bougé.

La calibration a inversé les deux hypothèses de départ
-------------------------------------------------------
Ce fichier a d'abord été écrit sur deux convictions. Les deux étaient fausses, et le
mode `--calibrer` les a défaites en trois minutes.

**« Un embedding est déterministe. »** Non. Mesuré le 28/08/2026 sur cinq tirages du
même texte : écart absolu maximal **2.6 × 10⁻⁴** entre deux appels. Aucun arrondi ne
stabilise une empreinte par hachage — testé de 3 à 6 décimales, toujours trois
empreintes distinctes sur cinq tirages, parce qu'un arrondi ne fait que déplacer la
frontière où le bruit bascule.

**« La génération à température 0 est bruitée. »** Non plus, pas sur ces questions :
cinq tirages, une seule réponse distincte pour chacune des trois.

La règle de comparaison est donc l'inverse de ce qui était prévu : **égalité stricte
pour la génération, similarité cosinus pour les embeddings.**

Pourquoi le cosinus, et à quel seuil
--------------------------------------
Le bruit de 2.6 × 10⁻⁴ ne déplace presque pas la direction du vecteur : la similarité
cosinus minimale mesurée entre deux tirages est **0.999999**. Un modèle d'embedding
différent produit un espace différent — la similarité s'effondre bien en dessous de 0.9.

Le seuil est posé à **0.9999**, soit dix fois la marge du bruit observé, et très loin
de tout changement réel de modèle.

**Un garde-fou dont on n'a pas mesuré le bruit propre est un générateur de fausses
alertes** — et une alerte qu'on apprend à ignorer ne protège plus rien. C'est la seule
raison d'être du mode `--calibrer`.

Usage
-----
    set -a; . ./.env; set +a

    python _chantier/scripts/canari_modeles.py --calibrer   # mesure la stabilite, pose l'empreinte
    python _chantier/scripts/canari_modeles.py              # verifie, sort 1 si derive
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
EMPREINTE = RACINE / "_chantier" / "canari.json"

BASE_CHAT = "https://llm.lab.sspcloud.fr/api"
BASE_EMBED = "https://albert.api.etalab.gouv.fr/v1"
MODELE_CHAT = "qwen3-6-35b-moe"
MODELE_EMBED = "BAAI/bge-m3"

# Textes courts et neutres. Ils ne mesurent aucune qualité — ils servent d'empreinte.
# Volontairement sans rapport avec le corpus de la référence : un canari qui porterait
# du droit de la commande publique se confondrait avec ce qu'il est censé garder.
# Pas de chaine vide : mesure le 28/08/2026, l'API Albert la refuse par
# « `inputs` cannot be empty ». A savoir pour l'indexation — un document vide fait
# echouer le lot entier, pas seulement son entree.
TEXTES_EMBED = [
    "Le chat dort sur le toit.",
    "deux plus deux",
    "Un texte un peu plus long, pour que la longueur varie d'une entrée à l'autre.",
]

QUESTIONS_CHAT = [
    "Réponds par un seul chiffre : combien font 2 + 2 ?",
    "Réponds par un seul mot : quelle est la capitale de la France ?",
    "Réponds par oui ou non : 10 est-il supérieur à 3 ?",
]

TIRAGES_CALIBRATION = 5


# Les deux cles ne vivent pas au meme endroit, et c'est le depot qui en decide :
# SSPCLOUD_API_KEY dans le .env du tronc, ALBERT_API_KEY dans celui de colaig-v3.
# Meme ordre que `reference_l15._cle` : l'environnement d'abord — c'est ainsi qu'un
# secret est fourni par une chaine d'integration — les fichiers ensuite, pour le
# confort du poste.
FICHIERS_ENV = (RACINE / ".env", RACINE.parent / "colaig-v3" / ".env")


def _cle(nom_env: str) -> str:
    for variante in (nom_env, nom_env.lower()):
        v = os.environ.get(variante)
        if v:
            return v.strip()
    for fichier in FICHIERS_ENV:
        try:
            for ligne in open(fichier, encoding="utf-8"):
                if ligne.strip().lower().startswith(nom_env.lower() + "="):
                    v = ligne.split("=", 1)[1].strip()
                    if v:
                        return v
        except OSError:
            continue
    raise SystemExit(
        f"{nom_env} introuvable — ni environnement, ni "
        + ", ".join(str(f) for f in FICHIERS_ENV))


def _poster(base: str, chemin: str, corps: dict, cle: str) -> dict:
    q = urllib.request.Request(base + chemin, data=json.dumps(corps).encode(),
                               method="POST")
    q.add_header("Authorization", "Bearer " + cle)
    q.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(q, timeout=120) as r:
        return json.loads(r.read().decode())


# ── Le canari des embeddings — déterministe ─────────────────────────────────


# Dix fois la marge du bruit mesuré (0.999999), et très loin de tout changement réel
# de modèle — un espace d'embedding différent effondre le cosinus bien sous 0.9.
COSINUS_MINIMUM = 0.9999


def vecteurs_canari(cle: str) -> tuple[list[list[float]], str]:
    d = _poster(BASE_EMBED, "/embeddings",
                {"model": MODELE_EMBED, "input": TEXTES_EMBED}, cle)
    return ([e["embedding"] for e in sorted(d["data"], key=lambda e: e["index"])],
            d.get("model", ""))


def cosinus(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    return num / den if den else 0.0


# ── Le canari de génération — bruité, donc calibré ──────────────────────────


def reponses_chat(cle: str) -> list[str]:
    sorties = []
    for question in QUESTIONS_CHAT:
        d = _poster(BASE_CHAT, "/chat/completions", {
            "model": MODELE_CHAT,
            "messages": [{"role": "user", "content": question}],
            "temperature": 0.0,
            "max_tokens": 12,
            "chat_template_kwargs": {"enable_thinking": False},
        }, cle)
        sorties.append((d["choices"][0]["message"].get("content") or "").strip())
    return sorties


def modele_chat_annonce(cle: str) -> str:
    d = _poster(BASE_CHAT, "/chat/completions", {
        "model": MODELE_CHAT,
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 4,
    }, cle)
    return d.get("model", "")


# ── Calibration ─────────────────────────────────────────────────────────────


def calibrer() -> int:
    cle_c, cle_e = _cle("SSPCLOUD_API_KEY"), _cle("ALBERT_API_KEY")

    print("embeddings — mesure du bruit propre", file=sys.stderr)
    tirages_e = [vecteurs_canari(cle_e)[0] for _ in range(3)]
    cos_min = min(cosinus(v0, v1)
                  for t in tirages_e[1:] for v0, v1 in zip(tirages_e[0], t))
    ecart_max = max(abs(x - y)
                    for t in tirages_e[1:] for v0, v1 in zip(tirages_e[0], t)
                    for x, y in zip(v0, v1))
    print(f"  ecart absolu maximal {ecart_max:.2e} · cosinus minimal {cos_min:.9f}",
          file=sys.stderr)
    if cos_min < COSINUS_MINIMUM:
        print(f"  BRUIT PROPRE SOUS LE SEUIL ({COSINUS_MINIMUM}) — ce canari "
              "produirait de fausses alertes. Ne pas l'utiliser en l'etat.",
              file=sys.stderr)
        return 2

    print(f"génération — {TIRAGES_CALIBRATION} tirages à température 0", file=sys.stderr)
    tirages = [reponses_chat(cle_c) for _ in range(TIRAGES_CALIBRATION)]
    stables = []
    for i, question in enumerate(QUESTIONS_CHAT):
        vues = {t[i] for t in tirages}
        stables.append(len(vues) == 1)
        print(f"  q{i + 1} : {len(vues)} réponse(s) distincte(s) — {sorted(vues)}",
              file=sys.stderr)

    vecteurs_ref, modele_e = vecteurs_canari(cle_e)
    donnees = {
        "_pourquoi": [
            "Les modeles distants n'exposent ni version ni empreinte : un changement de",
            "poids sous le meme nom rendrait toute la reference caduque EN SILENCE, et",
            "la porte de regression imputerait la derive a notre code.",
            "",
            "Ce fichier est l'empreinte des modeles au moment ou la reference a ete",
            "posee. Le canari la rejoue avant chaque campagne.",
        ],
        "_calibre_le": "a remplir par l'appelant",
        "embeddings": {
            "modele": MODELE_EMBED,
            "modele_annonce": modele_e,
            "textes": TEXTES_EMBED,
            "dimension": len(vecteurs_ref[0]),
            "cosinus_minimum": COSINUS_MINIMUM,
            "bruit_mesure": {"ecart_absolu_max": ecart_max,
                             "cosinus_min": cos_min, "tirages": 3},
            "vecteurs": [[round(x, 6) for x in v] for v in vecteurs_ref],
            "_note": (
                "UN EMBEDDING N'EST PAS DETERMINISTE : ecart absolu de 2.6e-04 mesure "
                "entre deux appels du meme texte, et aucun arrondi ne stabilise une "
                "empreinte par hachage — un arrondi ne fait que deplacer la frontiere "
                "ou le bruit bascule. La comparaison se fait donc par SIMILARITE "
                "COSINUS. C'est le modele le plus insidieux a changer : un bge-m3 "
                "remplace deplacerait toute la recherche sans qu'une seule reponse "
                "paraisse fausse."
            ),
        },
        "generation": {
            "modele": MODELE_CHAT,
            "modele_annonce": modele_chat_annonce(cle_c),
            "questions": QUESTIONS_CHAT,
            "reponses_stables": [t for t, ok in zip(tirages[0], stables) if ok],
            "questions_stables": [q for q, ok in zip(QUESTIONS_CHAT, stables) if ok],
            "questions_ecartees": [q for q, ok in zip(QUESTIONS_CHAT, stables) if not ok],
            "_note": (
                "Un service LLM n'est PAS deterministe meme a temperature 0 : "
                "regroupement des requetes et ordre des sommes flottantes sur GPU. "
                "Seules les questions dont la reponse s'est averee stable sur "
                f"{TIRAGES_CALIBRATION} tirages sont retenues — un canari dont on n'a "
                "pas mesure le bruit propre est un generateur de fausses alertes, et "
                "une alerte qu'on apprend a ignorer ne protege plus rien."
            ),
        },
    }
    EMPREINTE.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print(f"\nempreinte écrite : {EMPREINTE}", file=sys.stderr)
    print(f"  embeddings : comparés par cosinus, seuil {COSINUS_MINIMUM}",
          file=sys.stderr)
    print(f"  génération : {sum(stables)}/{len(QUESTIONS_CHAT)} questions retenues",
          file=sys.stderr)
    if not any(stables):
        print("\nAUCUN canari stable — ce dispositif ne peut rien garder. "
              "Ne pas s'en servir en l'etat.", file=sys.stderr)
        return 2
    return 0


# ── Vérification ────────────────────────────────────────────────────────────


def verifier() -> int:
    if not EMPREINTE.exists():
        raise SystemExit(f"{EMPREINTE.name} absent — lancer d'abord --calibrer.")
    ref = json.loads(EMPREINTE.read_text(encoding="utf-8"))
    cle_c, cle_e = _cle("SSPCLOUD_API_KEY"), _cle("ALBERT_API_KEY")
    derives = []

    attendu = ref["embeddings"]
    obtenus, _ = vecteurs_canari(cle_e)
    if len(obtenus[0]) != attendu["dimension"]:
        derives.append(
            f"embeddings : dimension {attendu['dimension']} → {len(obtenus[0])}")
    else:
        cos = min(cosinus(a, b) for a, b in zip(attendu["vecteurs"], obtenus))
        seuil = attendu.get("cosinus_minimum", COSINUS_MINIMUM)
        print(f"embeddings : cosinus {cos:.9f} (seuil {seuil})", file=sys.stderr)
        if cos < seuil:
            derives.append(
                f"embeddings : cosinus {cos:.6f} sous le seuil {seuil} — "
                f"le modèle {MODELE_EMBED} n'est plus le même")

    g = ref["generation"]
    servi = modele_chat_annonce(cle_c)
    if servi != g.get("modele_annonce"):
        derives.append(f"génération : nom servi {g.get('modele_annonce')!r} → {servi!r}")

    if g["questions_stables"]:
        obtenues = []
        for question in g["questions_stables"]:
            d = _poster(BASE_CHAT, "/chat/completions", {
                "model": MODELE_CHAT,
                "messages": [{"role": "user", "content": question}],
                "temperature": 0.0, "max_tokens": 12,
                "chat_template_kwargs": {"enable_thinking": False},
            }, cle_c)
            obtenues.append((d["choices"][0]["message"].get("content") or "").strip())
        for q, att, obt in zip(g["questions_stables"], g["reponses_stables"], obtenues):
            if att != obt:
                derives.append(f"génération : {q!r} → {att!r} devenu {obt!r}")
        print(f"génération : {len(obtenues)} question(s) rejouée(s)", file=sys.stderr)
    else:
        print("génération : aucune question stable — ce canari ne garde rien",
              file=sys.stderr)

    if derives:
        print("\nDÉRIVE DE MODÈLE :", file=sys.stderr)
        for d in derives:
            print(f"  {d}", file=sys.stderr)
        print("\nLes valeurs de reference.json ont été mesurées contre d'autres modèles.",
              file=sys.stderr)
        print("Ne pas imputer une régression au code avant d'avoir remesuré la référence.",
              file=sys.stderr)
        return 1

    print("\nAucune dérive de modèle.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(calibrer() if "--calibrer" in sys.argv else verifier())
