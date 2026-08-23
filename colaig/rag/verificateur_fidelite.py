"""
Colaig — vérificateur de fidélité : l'extrait étaye-t-il l'affirmation ?

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.6

Ce que ce module ajoute à `verification_citations.py`
-----------------------------------------------------
`verification_citations` contrôle la **provenance** — le numéro d'article cité
figure-t-il dans les passages fournis ? C'est décidable sans modèle, et c'est pour
cela qu'il est fiable. Sa docstring dit aussi ce qu'il ne sait pas faire : juger si
la réponse est **fidèle** au passage dont elle se réclame.

`mp-032` du jeu doré tient exactement dans cet écart. Le modèle répond à une question
dont la réponse précise n'est pas dans le corpus, en affirmant s'en tenir strictement
aux passages. La provenance est correcte : `R2191-3` est bien là. C'est l'**inférence**
qui déborde. Aucun contrôle mécanique ne l'attrapera jamais — il faut lire, donc un
modèle.

Les deux règles qui font la valeur du dispositif
-------------------------------------------------
Reprises du poste de rédaction `Editeur`, où elles ont été posées le 23/08/2026.

**1. La recette de contexte est la plus pauvre du système.** Une affirmation, un
extrait. Ni le nom du document, ni la page, ni l'autorité de la source, ni l'intention
de celui qui écrit. Un modèle à qui l'on dit que la source fait autorité trouve plus
facilement qu'elle étaye. La pauvreté du contexte ferme ce biais **par construction**,
là où une consigne ne ferait que le déconseiller — et l'on sait ce que vaut une
consigne : mesuré sur le jeu doré, un prompt qui interdisait déjà d'inventer laissait
passer des citations hors contexte.

Cette pauvreté est fragile : chaque ajout paraîtra utile sur le moment.
`test_le_modele_ne_recoit_que_l_affirmation_et_l_extrait` existe pour que l'ajout se
voie.

**2. Le passage d'appui est vérifié sans modèle.** Le vérificateur doit rendre une
portion **verbatim** de l'extrait ; le code contrôle qu'elle s'y trouve. C'est la seule
part du verdict qui ne dépend d'aucun jugement — et elle vaut précisément parce que le
vérificateur est lui aussi un modèle. On ne le croit pas sur parole au motif qu'il est
le contrôleur.

Ce que ce module ne fait pas
-----------------------------
Il ne dit pas si l'affirmation est **vraie**, seulement si l'extrait l'étaye. Une
affirmation peut être fidèle à un passage et fausse en droit — parce que le passage est
abrogé, ou parce qu'un autre article le corrige. Ce module ne le verra pas, et ne le
prétend pas.

# TODO-NORMALE : promouvoir en Protocol si un second implémenteur apparaît. Toucher
# `protocols.py` relève d'un arbitrage humain (CLAUDE.md §5) — pas fait ici.
"""
from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass

from colaig.rag.verification_citations import FORMAT_CODE, articles_cites

logger = logging.getLogger(__name__)

VERDICTS = ("etaye", "etaye_partiellement", "ne_dit_pas_cela", "contredit")
ILLISIBLE = "illisible"

CONSIGNE = """Tu compares une AFFIRMATION à un EXTRAIT, et tu dis si l'extrait l'étaye.
Rien d'autre. Tu ne rédiges pas, tu ne complètes pas, tu ne conseilles pas.

RÈGLES ABSOLUES
1. Tu ne juges que sur l'extrait fourni. Aucune connaissance extérieure, aucune
   inférence sur ce que la source devrait dire.
2. Tu ignores qui a écrit l'affirmation et d'où vient l'extrait. Ni l'un ni l'autre ne
   rend une affirmation plus vraie.
3. Le doute ne profite pas à l'affirmation. Si l'extrait ne dit pas explicitement ce
   qu'elle avance, le verdict est "ne_dit_pas_cela".
4. Un écart de portée compte : « devra » n'est pas « pourra », « peut » n'est pas
   « doit », « est invité à » n'est pas « est tenu de ». Un tel écart donne
   "etaye_partiellement" au mieux.
5. Le passage d'appui est une portion EXACTE de l'extrait, copiée telle quelle. Si
   aucune portion ne fonde le verdict, laisse-le vide.

VERDICT — une seule de ces quatre valeurs, jamais autre chose :
  etaye                l'extrait dit ce que l'affirmation avance
  etaye_partiellement  il le dit en partie, ou avec une portée différente
  ne_dit_pas_cela      l'extrait est muet sur ce point
  contredit            l'extrait dit le contraire

SORTIE : uniquement un objet JSON, sans balise ni commentaire :
{"verdict": "...", "motif": "une phrase, sans répéter l'affirmation",
 "passage_appui": "portion exacte de l'extrait, ou vide"}"""


@dataclass
class Fidelite:
    """Ce que le vérificateur a jugé, et ce qu'on peut en faire."""

    verdict: str
    motif: str
    passage_appui: str
    appui_dans_extrait: bool

    @property
    def etaye(self) -> bool:
        """L'extrait soutient-il pleinement l'affirmation ?"""
        return self.verdict == "etaye" and self.appui_dans_extrait

    @property
    def exploitable(self) -> bool:
        """Peut-on se fier à ce verdict ?

        Deux conditions. Le verdict doit être **dans la liste** — hors d'elle, c'est
        une panne du vérificateur, pas un jugement. Et un verdict **positif** doit être
        ancré : sans portion verbatim retrouvée dans l'extrait, il a été fabriqué.

        Un verdict négatif, lui, n'a pas besoin d'appui : l'absence est ce qu'il
        constate, et l'on ne cite pas ce qui manque.
        """
        if self.verdict not in VERDICTS:
            return False
        if self.verdict in ("etaye", "etaye_partiellement"):
            return self.appui_dans_extrait
        return True


def _normaliser(texte: str) -> str:
    """Espaces, retours à la ligne et casse sont des accidents de copie.

    Exiger l'égalité stricte ferait rejeter des appuis authentiques, et un garde-fou
    qui crie au loup trop souvent finit ignoré.
    """
    plat = (texte or "")
    # Variantes typographiques : apostrophe courbe, guillemets, tirets, espaces
    # insécables. Un modèle qui recopie un extrait en normalise volontiers la
    # typographie sans rien changer au texte — le refuser pour cela reviendrait à
    # exiger une copie octet à octet là où l'on veut vérifier des mots.
    for source, cible in (("’", "'"), ("‘", "'"),
                          ("«", '"'), ("»", '"'),
                          ("“", '"'), ("”", '"'),
                          ("–", "-"), ("—", "-"),
                          (" ", " "), (" ", " "), (" ", " ")):
        plat = plat.replace(source, cible)
    return " ".join(plat.split()).lower()


# Marques d'élision : le vérificateur cite souvent un appui **discontinu**.
_ELISION = re.compile(r"\s*(?:\[\s*\.\.\.\s*\]|\.\.\.|…|\[…\])\s*")
_FRAGMENT_MINIMAL = 25


def _ancre(appui: str, extrait: str) -> bool:
    """Chaque morceau de l'appui se retrouve-t-il verbatim dans l'extrait ?

    Le vérificateur cite volontiers un passage **discontinu**, en marquant la coupure
    par « [...] » — et c'est légitime : quand l'appui s'étend sur deux articles, il ne
    peut pas faire autrement sans recopier tout ce qui les sépare.

    Une comparaison par sous-chaîne exacte échoue alors sur la jointure. Mesuré sur
    30 couples fidèles par construction, elle déclarait **57 % d'appuis fabriqués** —
    aucun ne l'était. Un contrôle qui accuse à tort une fois sur deux est pire
    qu'absent : on cesse de le lire.

    Chaque fragment est donc vérifié séparément, et **tous** doivent se retrouver. Le
    seuil de longueur ferme la porte de sortie : sans lui, un appui fabriqué découpé en
    fragments de trois mots finirait par se retrouver quelque part dans un long extrait.
    """
    plein = _normaliser(extrait)
    morceaux = [_normaliser(m) for m in _ELISION.split(appui or "")]
    morceaux = [m for m in morceaux if len(m) >= _FRAGMENT_MINIMAL]
    if not morceaux:
        # Appui trop court pour être vérifié : on ne l'invente pas conforme.
        return bool(_normaliser(appui)) and _normaliser(appui) in plein
    return all(m in plein for m in morceaux)


def _objet(brut: str) -> dict:
    """L'objet rendu, ou vide si la sortie n'est pas exploitable.

    On ne devine jamais : une sortie illisible se dit, elle ne se rattrape pas.
    """
    net = brut.strip()
    if net.startswith("`"):
        net = net.strip("`")
        net = net[4:] if net.lower().startswith("json") else net
    try:
        return json.loads(net[net.index("{"): net.rindex("}") + 1])
    except Exception:
        logger.warning("vérificateur : sortie non exploitable (%d caractères)", len(brut))
        return {}


async def verifier_fidelite(affirmation: str, extrait: str, client) -> Fidelite:
    """L'extrait étaye-t-il l'affirmation ?

    Args:
        affirmation: la phrase à contrôler, telle qu'elle a été écrite.
        extrait: le passage dont elle se réclame, verbatim.
        client: un `LLMClientProtocol`.

    Le client ne reçoit **que** ces deux textes — voir la note en tête de module.
    """
    if not (affirmation or "").strip() or not (extrait or "").strip():
        raise ValueError(
            "il faut une affirmation ET un extrait : on ne vérifie pas dans le vide"
        )

    donnees = f"AFFIRMATION : {affirmation.strip()}\n\nEXTRAIT : {extrait.strip()}"
    brut = await client.chat(
        messages=[
            {"role": "system", "content": CONSIGNE},
            {"role": "user", "content": donnees},
        ],
        # Un contrôle qui varie d'une exécution à l'autre ne contrôle rien.
        temperature=0,
        max_tokens=4000,
        priority="background",
    )

    objet = _objet(brut)
    verdict = objet.get("verdict") if objet.get("verdict") in VERDICTS else ILLISIBLE
    appui = (objet.get("passage_appui") or "").strip()
    return Fidelite(
        verdict=verdict,
        motif=(objet.get("motif") or "").strip(),
        passage_appui=appui,
        appui_dans_extrait=bool(appui) and _ancre(appui, extrait),
    )


# ── Quand appeler le vérificateur, et sur quoi ──────────────────────────────

SEUIL_COUPLES = 3


def couples_a_verifier(reponse: str, passages: list[str],
                       formats: tuple[str, ...] = (FORMAT_CODE,)) -> list[tuple[str, str]]:
    """Découpe la réponse en couples (affirmation, extrait) vérifiables.

    Une réponse entière n'est pas une affirmation : la vérifier d'un bloc ne donnerait
    qu'un verdict global inexploitable — « partiellement étayé » ne dit pas *quelle*
    phrase déborde.

    Le découpage retenu : **une phrase portant une référence d'article, confrontée à la
    réunion des passages qui portent les articles qu'elle cite.**

    La réunion, et non le premier passage. Mesuré sur des couples fidèles par
    construction, l'appariement au premier article seul produisait **30 % de faux
    négatifs** : une phrase qui synthétise deux articles est jugée « ne dit pas cela »
    contre chacun pris isolément. Le vérificateur avait raison, le découpage avait tort.

    Les phrases sans référence sont écartées : `garde_fou_reponse` les traite déjà, en
    remplaçant par un refus toute réponse sans attache.
    """
    fournis = [(p, articles_cites(p, formats)) for p in passages]
    couples: list[tuple[str, str]] = []
    for phrase in re.split(r"(?<=[.;])\s+", " ".join((reponse or "").split())):
        if len(phrase) < 40:
            continue  # ni un titre, ni un fragment de liste : une règle s'énonce
        cites = articles_cites(phrase, formats)
        if not cites:
            continue
        morceaux = [p for p, arts in fournis if cites & arts]
        if morceaux:
            couples.append((phrase, "\n\n".join(dict.fromkeys(morceaux))))
    return couples


def merite_verification(couples: list[tuple[str, str]],
                        seuil: int = SEUIL_COUPLES) -> bool:
    """Cette réponse justifie-t-elle le coût d'une vérification de fidélité ?

    Le vérificateur coûte environ **une seconde par couple**, sur une réponse qui en
    prend deux. Le passer sur tout triplerait la latence gagnée en coupant le
    raisonnement du modèle (D18).

    **Le nombre de couples est à la fois le coût et le risque**, ce qui en fait le bon
    déclencheur. Mesuré sur 39 réponses dont 12 portent au moins un verdict négatif :

    | | réponses saines | réponses suspectes |
    |---|---|---|
    | couples à vérifier | 2 | **5,5** |
    | articles cités | 2 | **4** |
    | longueur | 956 car. | **1913 car.** |
    | score du 1er passage | 0,663 | 0,671 — *ne sépare pas* |
    | dispersion des scores | 0,114 | 0,105 — *ne sépare pas* |

    Seuil à 3 couples : **56 % des réponses vérifiées, 10 suspects sur 12 attrapés.**

    L'interprétation tient : une réponse longue citant beaucoup d'articles est une
    réponse où le modèle a **synthétisé**, et c'est là qu'il déborde. Une réponse courte
    qui cite un article et s'arrête reste fidèle.

    **Ce qui ne marche pas mérite d'être retenu** : la dispersion des scores de
    recherche prédit l'échec de *récupération* (D11) et **ne prédit pas** l'infidélité.
    Deux modes de défaillance, deux signaux — s'en servir ici aurait paru naturel et
    n'aurait rien attrapé : 2 suspects sur 12 au même taux d'appel.

    Échantillon de 39 réponses : le sens de la séparation est net, le seuil exact ne
    l'est pas. À remesurer avant d'en faire une constante de production.
    """
    return len(couples) >= seuil
