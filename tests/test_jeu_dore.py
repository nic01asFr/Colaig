"""
Contrat du jeu doré — L1.4.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.4

Le jeu doré est l'**instrument de mesure** de tout ce qui suivra : c'est contre lui que
L1.5 établit la référence, et contre cette référence que chaque modification du pipeline
se juge. Un instrument faussé est pire qu'une absence d'instrument, parce qu'il produit
des chiffres qu'on croit.

Ces tests n'évaluent pas Colaig. Ils vérifient le **jeu doré** :

- chaque article cité existe bel et bien dans le corpus, à l'identique ;
- le corpus n'a pas dérivé depuis que les cas ont été écrits (manifeste d'empreintes) ;
- la forme de chaque cas est complète et cohérente ;
- les cas négatifs — ceux dont la réponse n'est **pas** dans le corpus — sont présents
  en nombre suffisant.

Sur les cas négatifs
--------------------
Ce sont les plus importants, et ceux qu'on oublie. Un jeu doré composé de questions
répondables ne mesure que la capacité à répondre ; il ne mesure jamais la capacité à
**se taire**. Or sur un corpus juridique, une réponse inventée — un seuil, un délai, une
référence de jurisprudence — produit une procédure irrégulière. C'est l'échec le plus
coûteux, et le seul qu'un jeu doré naïf laisse passer.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent
CORPUS = RACINE / "golden" / "corpus-marches-publics"
JEU = RACINE / "golden" / "v1.jsonl"

CHAMPS_REQUIS = {"id", "type", "difficulte", "question", "reponse_attendue",
                 "articles_attendus", "justification"}
TYPES = {"fait", "procedure", "redaction", "piege"}
DIFFICULTES = {"simple", "croisee", "negative"}


def _cas() -> list[dict]:
    return [json.loads(ligne) for ligne in JEU.read_text(encoding="utf-8").splitlines() if ligne.strip()]


def _articles_du_corpus() -> set[str]:
    """Tous les numéros d'article réellement présents dans les documents."""
    numeros: set[str] = set()
    for fichier in CORPUS.glob("*.md"):
        numeros.update(re.findall(r"^## Article ([A-Za-z0-9\- ]+)$", fichier.read_text(encoding="utf-8"), re.M))
    return {n.strip() for n in numeros}


# ── Le corpus n'a pas bougé ─────────────────────────────────────────────────


def test_le_corpus_correspond_a_son_manifeste():
    """Le jeu doré a été écrit contre CE corpus. S'il change, les réponses peuvent devenir fausses.

    C'est le mode de dérive le plus insidieux : un article modifié, et une réponse
    attendue devient incorrecte sans qu'aucun test n'échoue — la référence dérive en
    silence et l'on continue de mesurer contre elle.
    """
    manifeste = CORPUS / "MANIFESTE.txt"
    assert manifeste.is_file(), "manifeste absent : la dérive du corpus serait indétectable"

    attendus = {}
    for ligne in manifeste.read_text(encoding="utf-8").splitlines():
        if ligne.startswith("#") or not ligne.strip():
            continue
        empreinte, _taille, nom = ligne.split(None, 2)
        attendus[nom.strip()] = empreinte

    ecarts = []
    for nom, empreinte in attendus.items():
        fichier = CORPUS / nom
        if not fichier.is_file():
            ecarts.append(f"  manquant : {nom}")
            continue
        reelle = hashlib.sha256(fichier.read_bytes()).hexdigest()[:16]
        if reelle != empreinte:
            ecarts.append(f"  modifié : {nom}")

    presents = {f.name for f in CORPUS.glob("*.md")}
    for surnumeraire in sorted(presents - set(attendus)):
        ecarts.append(f"  non déclaré au manifeste : {surnumeraire}")

    assert not ecarts, (
        "le corpus a dérivé depuis l'écriture du jeu doré :\n" + "\n".join(ecarts)
        + "\n\nRevérifier les réponses attendues avant de régénérer le manifeste."
    )


# ── Chaque cas est ancré dans le corpus ─────────────────────────────────────


def test_tous_les_articles_cites_existent():
    """Un cas qui cite un article inexistant est pire qu'un cas manquant.

    Il rend le jeu doré inatteignable : aucun moteur ne peut retrouver ce qui n'est
    pas là, et l'on conclurait à une défaillance du pipeline.
    """
    disponibles = _articles_du_corpus()
    assert disponibles, "aucun article détecté dans le corpus — le parsing est cassé"

    fautifs = []
    for cas in _cas():
        for champ in ("articles_attendus", "articles_utiles"):
            for article in cas.get(champ, []):
                if article not in disponibles:
                    fautifs.append(f"  {cas['id']} ({champ}) cite « {article} », absent du corpus")
    assert not fautifs, "\n".join(fautifs)


def test_les_reponses_chiffrees_se_retrouvent_dans_le_corpus():
    """Tout montant ou délai d'une réponse attendue doit figurer dans un article cité.

    C'est le contrôle qui empêche d'écrire le jeu doré **de mémoire**. Il a servi :
    le seuil de dispense de publicité vaut 60 000 € (fournitures) et 100 000 €
    (travaux), pas les 40 000 € que l'on cite souvent — chiffre qui, dans le code
    actuel, désigne la publication des données essentielles.
    """
    textes = {}
    for fichier in CORPUS.glob("*.md"):
        contenu = fichier.read_text(encoding="utf-8")
        for bloc in re.split(r"(?=^## Article )", contenu, flags=re.M):
            m = re.match(r"## Article ([A-Za-z0-9\- ]+)", bloc)
            if m:
                textes.setdefault(m.group(1).strip(), "")
                textes[m.group(1).strip()] += bloc

    fautifs = []
    for cas in _cas():
        if cas.get("attendu_refus"):
            continue  # un cas négatif n'a pas de chiffre à retrouver
        cites = " ".join(
            textes.get(a, "") for a in cas.get("articles_attendus", []) + cas.get("articles_utiles", [])
        )
        normalise = cites.replace(" ", " ").replace("\xa0", " ")
        # Un montant repris de la question — « mon marché de 70 000 € » — n'est pas une
        # affirmation de droit : c'est l'hypothèse posée par l'utilisateur. Seuls les
        # chiffres que la réponse **avance** doivent se retrouver dans un article cité.
        poses_par_la_question = set(re.findall(r"\b\d{1,3}(?: \d{3})+\b", cas["question"]))
        for montant in re.findall(r"\b\d{1,3}(?: \d{3})+\b", cas["reponse_attendue"]):
            if montant in poses_par_la_question:
                continue
            if montant not in normalise:
                fautifs.append(f"  {cas['id']} : le montant « {montant} » n'est dans aucun article cité")
    assert not fautifs, "\n".join(fautifs)


# ── Forme des cas ───────────────────────────────────────────────────────────


def test_la_forme_de_chaque_cas_est_complete():
    vus = set()
    problemes = []
    for numero, cas in enumerate(_cas(), 1):
        manquants = CHAMPS_REQUIS - set(cas)
        if manquants:
            problemes.append(f"  ligne {numero} : champs manquants {sorted(manquants)}")
            continue
        if cas["id"] in vus:
            problemes.append(f"  identifiant en double : {cas['id']}")
        vus.add(cas["id"])
        if cas["type"] not in TYPES:
            problemes.append(f"  {cas['id']} : type inconnu « {cas['type']} »")
        if cas["difficulte"] not in DIFFICULTES:
            problemes.append(f"  {cas['id']} : difficulté inconnue « {cas['difficulte']} »")
        if not cas["question"].strip().endswith(("?", "»", ".")):
            problemes.append(f"  {cas['id']} : la question ne se termine pas comme une question")
        if len(cas["justification"]) < 40:
            problemes.append(f"  {cas['id']} : justification trop courte pour être vérifiable")
    assert not problemes, "\n".join(problemes)


def test_un_cas_negatif_ne_promet_aucun_article():
    """Un cas dont la réponse n'est pas dans le corpus ne peut pas exiger d'article.

    Sauf ceux qui **fondent le refus** — L2124-1 renvoie explicitement les seuils à un
    avis annexé, et c'est cette référence-là qu'une bonne réponse cite.
    """
    for cas in _cas():
        if not cas.get("attendu_refus"):
            continue
        assert cas["difficulte"] == "negative", f"{cas['id']} : refus attendu mais difficulté non négative"
        # Le contrôle porte sur le **sens** — dire que l'information manque — pas sur
        # une formulation. Une première version exigeait la locution exacte
        # « ne figure pas » et refusait « ne figurent pas » : un test qui impose une
        # tournure fait réécrire les cas pour lui plaire, au lieu de les vérifier.
        marqueurs = (
            "corpus", "ne figure pas", "ne figurent pas", "ne permet pas de répondre",
            "n'y sont pas", "ne se déduit", "ne relève pas",
        )
        reponse = cas["reponse_attendue"].lower()
        assert any(m in reponse for m in marqueurs), (
            f"{cas['id']} : la réponse attendue doit dire explicitement que "
            "l'information manque — aucun marqueur reconnu"
        )


def test_les_cas_negatifs_sont_assez_nombreux():
    """Un jeu doré sans cas négatifs ne mesure que la capacité à répondre.

    Il ne mesure jamais la capacité à **se taire** — alors qu'un seuil inventé produit
    une procédure irrégulière. Seuil retenu : au moins un cas sur six.
    """
    cas = _cas()
    negatifs = [c for c in cas if c.get("attendu_refus")]
    assert len(negatifs) >= max(2, len(cas) // 6), (
        f"seulement {len(negatifs)} cas négatifs sur {len(cas)} — "
        "le jeu ne mesure pas le refus d'inventer"
    )


def test_la_couverture_des_types_est_annoncee(capsys):
    """Ne fait échouer rien : rend la composition visible dans le rapport de test."""
    import collections

    cas = _cas()
    types = collections.Counter(c["type"] for c in cas)
    difficultes = collections.Counter(c["difficulte"] for c in cas)
    print(f"\njeu doré v1 : {len(cas)} cas")
    print(f"  types       : {dict(types)}")
    print(f"  difficultés : {dict(difficultes)}")
    print(f"  cible du plan : ≥ 200 cas — **non atteinte**, méthode établie sur {len(cas)}")
    assert len(cas) >= 20, "le jeu doré est en dessous de son amorce"
