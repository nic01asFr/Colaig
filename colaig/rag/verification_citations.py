"""
Colaig — vérification des citations d'une réponse.

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.5

Contrôle **mécanique** de la provenance des références citées dans une réponse.

Pourquoi il faut un contrôle et pas une consigne
------------------------------------------------
Mesuré sur 45 cas du jeu doré, avec le prompt système de l'espace qui interdit déjà
d'inventer : **10 réponses citent un article réel absent des passages fournis**. Le RAG
est alors contourné — la réponse vient de l'entraînement du modèle, pas du corpus.

Trois observations de cette mesure :

- une réponse cite `L1414-3`, article du **Code général des collectivités territoriales**,
  dans un corpus qui ne contient que le Code de la commande publique ;
- un cas négatif cite trois références différentes à chaque exécution — signature d'une
  fabrication, pas d'une erreur ;
- une réponse cite **l'article exact attendu** alors qu'il n'était pas dans les passages.
  Bonne réponse, mauvaise provenance : juste aujourd'hui, faux le jour où le texte
  changera sans que le corpus ait été relu.

Ce dernier cas est décisif. **La justesse apparente d'une réponse ne prouve rien sur sa
provenance**, et seul un contrôle qui compare les citations aux passages le détecte.
Une consigne, même explicite, se respecte « la plupart du temps » — ce qui, sur du droit,
ne suffit pas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Un numéro d'article, sous ses graphies usuelles : « L2113-10 », « L. 2113-10 »,
# « article R. 2122-8 » — mais aussi « L2 », « L. 3-1 ».
#
# Les articles **préliminaires** du code (L1 à L6, L3-1) définissent *contrat de la
# commande publique*, *marché*, *marché public*, *acheteur* : ce sont les plus cités par
# un assistant à la rédaction. Un motif exigeant quatre chiffres ne les voyait pas.
#
# L'angle mort n'était pas une simple lacune, il était **destructeur** : une réponse
# entièrement fondée qui n'aurait cité que « L2 » était vue comme ne citant rien, donc
# remplacée par un refus dans `garde_fou_reponse.appliquer()`. Le garde-fou détruisait la
# bonne réponse qu'il était censé protéger.
#
# Le motif élargi ajoute 229 occurrences sur le corpus figé (4519 → 4748). Elles ont été
# relues : références à d'autres codes citées **dans** les articles du CCP (code de
# commerce, code monétaire et financier) et articles préliminaires. Aucun faux positif de
# prose, à une coquille près dans la source — « L. 2339 11-1 », espace au lieu d'un tiret
# — qui produit une entrée fantôme côté passages, donc sans effet : elle ne peut que
# rendre le contrôle plus permissif d'une référence que personne ne citera.
#
# UN NOMBRE A QUATRE CHIFFRES SANS TIRET N'EST PAS UN ARTICLE.
#
# Le corpus ne compte que six articles en forme courte — L1 a L6, plus L3-1. Tout
# autre article du code porte quatre chiffres ET un tiret. « R2161 » ou « L2123 »
# designent donc une SECTION : « les articles R2161 et suivants » est une facon
# legitime d'ecrire, et la compter comme une citation d'article la faisait ensuite
# ressortir en fantome.
#
# Mesure : sur 124 cas, quatre des dix fantomes annonces etaient de cette nature. Une
# metrique qui signale comme invention une maniere correcte d'ecrire gonfle son propre
# compte et perd la confiance qu'on lui accorde.
#
# Les formes courtes restent reconnues, et c'est necessaire dans les deux sens : L2 est
# un vrai article, et « L30 » — cite par une reponse mesuree — est une vraie invention.
_MOTIF_ARTICLE = re.compile(r"\b([LRD])\.?\s?(\d{4}-\d+(?:-\d+)?|\d{1,3}(?:-\d+)?)\b")


# Un article de cahier des clauses : « article 20.1 », « 46.2.3 ».
#
# LE FORMAT DE CITATION EST UNE PROPRIÉTÉ DU CORPUS, pas du code.
#
# Le motif ci-dessus est celui du Code de la commande publique. Les **CCAG** — cahiers
# des clauses administratives générales, qui sont des arrêtés — numérotent tout
# autrement : « article 20.1 ». Une réponse citant le CCAG était donc vue comme ne
# citant **rien**, et `garde_fou_reponse.appliquer()` l'aurait remplacée par un refus :
# exactement le mode de défaillance corrigé pour les articles préliminaires, transposé
# à un autre corpus.
#
# Ce second motif n'est **pas actif par défaut**, et c'est mesuré : sur le corpus du
# code, `\d+\.\d+` relève 188 occurrences, toutes « 2.0 » — la mention « Licence
# Ouverte 2.0 » du pied de page. Inoffensif ici, mais sur un fonds de procédures ou de
# notes internes, « 2.5 » est un taux, un numéro de version, une date. Un contrôle qui
# prendrait ces fragments pour des citations déclarerait ancrées des réponses qui ne le
# sont pas — il serait pire qu'absent.
#
# Le format se déclare donc par corpus, comme le garde-fou lui-même (D19).
# Porté le 01/09/2026 : `WorkspaceConfig.format_citation`, lu depuis `config.yaml` et
# filtré sur `FORMATS_CONNUS` — un nom inconnu y lèverait un KeyError à chaque
# génération, et une faute de frappe rendrait l'espace muet.
#
# Ce que le format change, mesuré le 01/09 sur les 179 réponses archivées du cœur :
# sans « clause », le garde-fou remplace par un refus la réponse juste de mp-013, qui
# citait « Article 4.1 » du CCAG Travaux. Avec, aucune bonne réponse n'est détruite.
_MOTIF_CLAUSE = re.compile(r"\b(\d{1,2}(?:\.\d{1,2}){1,2})\b")

FORMAT_CODE = "code"          # L2113-10, R. 2122-8 — codes juridiques
FORMAT_CLAUSE = "clause"      # 20.1, 46.2.3 — CCAG, CCTG, cahiers de clauses

_MOTIFS = {FORMAT_CODE: _MOTIF_ARTICLE, FORMAT_CLAUSE: _MOTIF_CLAUSE}

# Publique, parce qu'un espace declare son format dans `config.yaml` : ce qui vient
# d'un fichier de configuration doit pouvoir etre valide avant d'atteindre `_MOTIFS`,
# ou une faute de frappe rendrait l'espace muet a chaque generation.
FORMATS_CONNUS = frozenset(_MOTIFS)


# Un identifiant de corpus se decompose en « <cahier> <numero> » : « CCAG Travaux 4 »,
# « CCAG Prestations intellectuelles 3 ». Le suffixe « — texte N » que porte une annexe
# vient du decoupage, pas du nom.
_IDENTIFIANT_NUMEROTE = re.compile(r"^(?P<cahier>.+?) (?P<numero>\d{1,3})$")
_SUFFIXE_DE_DECOUPAGE = re.compile(r"\s*—\s*texte\s+\d+$", re.I)

# « article 4.1 », « art. 41 », « Article 3 » — le sous-numero appartient a son article.
_ARTICLE_EN_TEXTE = re.compile(r"\bart(?:icle|\.)?\s+(\d{1,3})(?:\.\d{1,2})*\b", re.I)

# Distance maximale, en caracteres, entre le numero d'article et le nom du cahier.
# Sans elle, tout texte mentionnant un cahier citerait tous ses articles ; trop large,
# elle rapprocherait l'article d'un cahier du nom d'un autre — le corpus en porte
# quatre dont les articles portent les memes numeros.
_PORTEE_DU_RAPPROCHEMENT = 60


def _en_forme_naturelle(texte: str, identifiants) -> set:
    """Identifiants cites sous la forme du metier plutot que sous celle du corpus.

    Le corpus NOMME ses articles « CCAG Travaux 4 » parce que c'est ainsi qu'il les
    indexe. Un redacteur ecrit « l'article 4.1 du CCAG Travaux ». La recherche
    litterale ne rapproche pas les deux, et une reponse exacte compte pour un echec —
    releve le 05/09/2026 sur cinq cas dores au moins, et le garde-fou s'en sert aussi :
    une citation qu'il ne voit pas est une reponse qu'il peut affaiblir a tort.

    Le rapprochement exige le nom du cahier a PROXIMITE IMMEDIATE du numero, et refuse
    si le nom d'un autre cahier s'interpose : « l'article 3 du CCAG Prestations
    intellectuelles » ne cite pas « CCAG Travaux 3 ».
    """
    plat = " ".join((texte or "").split())
    trouves = set()

    par_cahier = {}
    for identifiant in identifiants:
        m = _IDENTIFIANT_NUMEROTE.match(identifiant.strip())
        if m:
            par_cahier.setdefault(m.group("cahier"), {})[m.group("numero")] = identifiant
    cahiers = sorted(par_cahier, key=len, reverse=True)

    for occurrence in _ARTICLE_EN_TEXTE.finditer(plat):
        numero = occurrence.group(1)
        debut = max(0, occurrence.start() - _PORTEE_DU_RAPPROCHEMENT)
        fin = occurrence.end() + _PORTEE_DU_RAPPROCHEMENT
        voisinage = plat[debut:fin]
        # Le cahier le plus long d'abord : « CCAG Fournitures et services » avant
        # « CCAG », faute de quoi un prefixe commun capterait tout.
        candidats = [c for c in cahiers if c in voisinage]
        if len(candidats) != 1:
            continue          # aucun cahier nomme, ou plusieurs : on ne tranche pas
        identifiant = par_cahier[candidats[0]].get(numero)
        if identifiant:
            trouves.add(identifiant)

    # Une annexe citee sans le suffixe que le decoupage lui a ajoute.
    #
    # SEULEMENT SI LE NOM AINSI OBTENU NE DESIGNE QUE CELLE-LA. « CCAG Travaux —
    # texte 1 » donnerait « CCAG Travaux », qui prefixe les quarante articles du meme
    # cahier : toute mention du cahier aurait alors cite ce texte. « Annexe 2 — Seuils
    # de procedure », lui, ne prefixe rien d'autre.
    for identifiant in identifiants:
        nom = _SUFFIXE_DE_DECOUPAGE.sub("", identifiant).strip()
        if nom and nom != identifiant and nom not in par_cahier and nom in plat:
            trouves.add(identifiant)

    return trouves


def _litteraux(texte: str, identifiants) -> set[str]:
    """Identifiants du corpus retrouvés **littéralement** dans un texte.

    Certains corpus ne numérotent pas leurs articles selon un motif. « CCAG Travaux 4 »,
    « Annexe 2 — Seuils de procédure — texte 1 » : aucune expression régulière ne les
    décrit, et en écrire une qui les couvre attraperait la moitié de la prose.

    Mais ces identifiants sont **connus** — le corpus les porte en en-tête. On les
    cherche donc tels quels, ce qui est à la fois exact et sans faux positif possible.

    Les plus longs d'abord, et la frontière de mot est nécessaire : sans elle,
    « CCAG Travaux 4 » se retrouverait à l'intérieur de « CCAG Travaux 41 », et une
    citation juste en produirait une fausse.
    """
    plat = " ".join((texte or "").split())
    trouves: set[str] = set()
    for identifiant in sorted(identifiants, key=len, reverse=True):
        motif = re.escape(" ".join(identifiant.split()))
        # La frontière interdit ce qui PROLONGE le numéro — un chiffre, un « .4 », un
        # « -4 » — et rien d'autre. Une première version excluait tout point : « CCAG
        # Travaux 41. » en fin de phrase n'était alors plus reconnu, et une citation
        # parfaitement formée passait pour absente.
        if re.search(motif + r"(?![0-9]|\.[0-9]|-[0-9])", plat):
            trouves.add(identifiant)
    return trouves


def articles_cites(texte: str, formats: tuple[str, ...] = (FORMAT_CODE,),
                   identifiants=()) -> set[str]:
    """Numéros d'article mentionnés dans un texte, normalisés (`L2113-10`).

    Args:
        texte: le texte à examiner.
        formats: formats de citation reconnus. Par défaut celui des codes juridiques.
            Ajouter `FORMAT_CLAUSE` pour un corpus de cahiers de clauses — voir la note
            ci-dessus sur les faux positifs qu'il entraîne ailleurs.
        identifiants: identifiants propres au corpus, cherchés **littéralement**. C'est
            la voie des textes qui ne numérotent pas selon un motif — les CCAG, les
            annexes. Exacte par construction, puisqu'elle ne cherche que ce qui existe.
    """
    trouves: set[str] = set()
    for nom in formats:
        motif = _MOTIFS[nom]
        for capture in motif.findall(texte or ""):
            trouves.add("".join(capture) if isinstance(capture, tuple) else capture)
    if identifiants:
        trouves |= _litteraux(texte, identifiants)
        trouves |= _en_forme_naturelle(texte, identifiants)
    return trouves


@dataclass
class Verification:
    """Résultat du contrôle de provenance d'une réponse."""

    citations: set[str] = field(default_factory=set)
    fournies: set[str] = field(default_factory=set)
    hors_contexte: set[str] = field(default_factory=set)

    @property
    def conforme(self) -> bool:
        """Toutes les citations proviennent-elles des passages fournis ?"""
        return not self.hors_contexte

    def avertissement(self) -> str:
        """Mention à joindre à la réponse quand elle ne l'est pas."""
        if self.conforme:
            return ""
        refs = ", ".join(sorted(self.hors_contexte))
        pluriel = "s" if len(self.hors_contexte) > 1 else ""
        return (
            f"\n\n---\n\n⚠️ **Référence{pluriel} non vérifiable{pluriel} : {refs}.** "
            f"{'Ces articles ne figurent pas' if len(self.hors_contexte) > 1 else 'Cet article ne figure pas'} "
            "dans les documents consultés pour cette réponse. "
            f"{'Ils proviennent' if len(self.hors_contexte) > 1 else 'Il provient'} "
            "de la mémoire du modèle et non du corpus : à vérifier avant tout usage."
        )


def verifier(reponse: str, passages: list[str],
             formats: tuple[str, ...] = (FORMAT_CODE,),
             identifiants=()) -> Verification:
    """Compare les articles cités dans la réponse à ceux présents dans les passages.

    `formats` doit être le **même des deux côtés** : reconnaître une graphie dans la
    réponse et pas dans les passages ferait passer pour hors contexte des citations
    légitimes. C'est déjà arrivé — deux motifs divergents avaient fait conclure à tort
    que le modèle puisait dans sa mémoire.
    """
    citations = articles_cites(reponse, formats, identifiants)
    fournies: set[str] = set()
    for passage in passages:
        fournies |= articles_cites(passage, formats, identifiants)
    return Verification(
        citations=citations,
        fournies=fournies,
        hors_contexte=citations - fournies,
    )


def annoter(reponse: str, passages: list[str]) -> tuple[str, Verification]:
    """Réponse assortie d'un avertissement si elle cite hors des passages.

    **Annoter plutôt que supprimer.** Retirer la référence rendrait la réponse plus
    propre et moins vérifiable : l'utilisateur perdrait l'information qui lui permet de
    contrôler. Une mention visible lui laisse la décision, ce qui est le bon partage —
    c'est lui qui engage sa procédure, pas l'assistant.
    """
    verification = verifier(reponse, passages)
    return reponse + verification.avertissement(), verification
