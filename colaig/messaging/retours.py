"""
Colaig — retours utilisateur par réaction (L3.3).

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0

Le dessin
---------
Colaig **pose lui-même** les quatre gestes sous chacune de ses réponses. Répondre coûte
alors un seul tapotement sur une réaction déjà présente, au lieu d'ouvrir un sélecteur
d'emoji et d'y chercher le bon. C'est la différence entre un retour que l'on obtient et
un retour que l'on espère.

    👍  la réponse convient          → retour persisté
    👎  la réponse ne convient pas   → retour persisté
    🔄  reformule                    → le tour est rejoué
    ➕  garde ça                     → la réponse est versée dans `.colaig/notes.md`

⚠️ `notes.md` vit sous `.colaig/`, que l'indexation écarte : les notes se relisent, mais
ne ressortent pas d'une recherche. C'est l'emplacement que fixe le lot ; le déplacer à
la racine de l'espace les rendrait interrogeables et relève d'un arbitrage produit.

Cette pose automatique impose la règle que `matrix.py` applique en amont : **les
réactions de Colaig ne remontent pas**. Sans ce filtre, chaque réponse s'attribuerait
quatre retours à elle-même, et le premier chiffre lu sur la qualité serait entièrement
fabriqué par nous.

Un fichier par geste
---------------------
Un journal unique se lit, se modifie et se réécrit. Deux personnes qui approuvent la
même réponse en même temps produiraient deux lectures du même état et **une seule des
deux écritures survivrait** — le second retour disparaîtrait sans trace ni erreur.

Un fichier par geste n'a pas de lecture préalable, donc pas de course. C'est déjà
l'idiome des conversations et des tâches dans ce dépôt, et c'est ce qui donne au
critère du lot — « le feedback survit au redémarrage » — un sens vérifiable.

Ce que ce module NE fait pas
------------------------------
La table `message → (espace, question, réponse)` est **en mémoire et bornée**. Après un
redémarrage, ➕ et 🔄 sur une réponse ancienne ne retrouvent rien, et ne font rien.

L'espace, lui, se retient **par conversation** : un geste sur une réponse ancienne du
même salon reste donc classé au bon endroit tant que le processus vit. Après un
redémarrage, la première réponse donnée dans ce salon le repeuple.

Résoudre l'espace au moment du geste — comme le fait le resolver pour un message —
serait plus robuste et coûterait une dépendance de plus ; c'est le chemin à prendre si
le besoin s'en fait sentir, pas une dette cachée.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from colaig import paths
from colaig.capacites import GARDER, GESTES, POUCE, POUCE_BAS, REJOUER
from colaig.models import Reaction

logger = logging.getLogger(__name__)

# ── Les quatre gestes ───────────────────────────────────────────────────────
#
# Ils sont DÉCLARÉS dans `colaig/capacites.py`, avec le texte qui les explique, et
# seulement réexportés ici. Le module les définissait auparavant pour son compte : le
# code émettait alors 🔁 (U+1F501) quand l'aide et le PLAN annonçaient 🔄 (U+1F504) —
# la campagne d'usage du 29/08/2026 a trouvé la divergence sur le fil. Un geste que
# Colaig pose et un geste qu'il explique doivent venir du même endroit.

__all__ = ["POUCE", "POUCE_BAS", "REJOUER", "GARDER", "GESTES_PROPOSES",
           "GESTES_DE_JUGEMENT", "GestionnaireRetours", "proposer_gestes",
           "lire_retours"]

# L'ordre est celui de l'affichage sous la réponse : d'abord juger, puis agir.
GESTES_PROPOSES: tuple[str, ...] = tuple(emoji for emoji, _ in GESTES)

# Ceux qui comptent comme un jugement sur la réponse. 🔄 et ➕ n'en sont pas : l'un est
# une demande, l'autre un classement. Les verser dans les retours brouillerait la seule
# mesure de qualité issue des usages réels.
GESTES_DE_JUGEMENT: tuple[str, ...] = (POUCE, POUCE_BAS)

# Combien de tours l'instance retient. Un processus qui tourne des semaines ne peut pas
# garder toutes ses réponses en mémoire — même exigence que les registres de `matrix.py`.
_MAX_MESSAGES_RETENUS = 512

# Combien de gestes déjà traités l'on retient, pour qu'une redélivraison Matrix ne
# double pas une note. Le dédoublonnage des RETOURS, lui, tient au nom du fichier : il
# survit au redémarrage, ce que cette table ne fait pas.
_MAX_GESTES_RETENUS = 2048


@dataclass
class Tour:
    """Ce qu'il faut savoir d'une réponse pour agir sur un geste qui la vise.

    `sources` et `confiance` ne servent pas a rejouer le tour : ils servent a le
    JUGER. Sans eux, un 👎 enregistre dit qu'une question a ete mal traitee sans
    dire ce que Colaig avait repondu — et le salon d'origine est chiffre, donc
    irrelisible autrement. Releve le 30/08/2026 sur le seul retour existant.
    """
    espace: str
    question: str
    reponse: str
    sources: tuple[str, ...] = ()
    confiance: float | None = None


async def proposer_gestes(messaging: Any, conversation_id: str) -> None:
    """Pose les quatre gestes sous la dernière réponse envoyée dans ce salon.

    Sans effet si le canal ne sait pas réagir — la capacité se teste structurellement
    (`ReactionProtocol`), pas par un drapeau de configuration.
    """
    from colaig.protocols import ReactionProtocol

    if not isinstance(messaging, ReactionProtocol):
        return

    message_id = messaging.dernier_message_envoye(conversation_id)
    if not message_id:
        return

    for emoji in GESTES_PROPOSES:
        await messaging.reagir(conversation_id, message_id, emoji)


class GestionnaireRetours:
    """Reçoit les gestes et en tire les conséquences."""

    def __init__(
        self,
        storage: Any,
        *,
        rejouer: Callable | None = None,
    ) -> None:
        self._storage = storage
        self._rejouer = rejouer
        self._messages: dict[str, Tour] = {}
        self._espaces: dict[str, str] = {}
        self._traites: dict[str, None] = {}

    # ── Mémoire des tours ───────────────────────────────────────────────

    def retenir(
        self,
        message_id: str,
        *,
        conversation_id: str = "",
        espace: str = "",
        question: str = "",
        reponse: str = "",
        sources: list[str] | tuple[str, ...] = (),
        confiance: float | None = None,
    ) -> None:
        """Retient de quoi agir sur un geste visant cette réponse — et de quoi le juger."""
        if not message_id:
            return
        self._messages.pop(message_id, None)          # remettre en tête
        self._messages[message_id] = Tour(espace=espace, question=question,
                                          reponse=reponse,
                                          sources=tuple(sources or ()),
                                          confiance=confiance)
        while len(self._messages) > _MAX_MESSAGES_RETENUS:
            self._messages.pop(next(iter(self._messages)))

        if conversation_id and espace:
            self._espaces.pop(conversation_id, None)
            self._espaces[conversation_id] = espace
            while len(self._espaces) > _MAX_MESSAGES_RETENUS:
                self._espaces.pop(next(iter(self._espaces)))

    def retrouver(self, message_id: str) -> Tour | None:
        return self._messages.get(message_id)

    # ── Traitement d'un geste ───────────────────────────────────────────

    async def traiter(self, reaction: Reaction) -> None:
        """Agit sur un geste — et **seulement** sur les quatre que Colaig propose.

        Un salon vit sa vie : 🎉 sur une réponse n'est pas une instruction. N'agir que
        sur les gestes proposés, c'est refuser qu'un contenu extérieur choisisse ce que
        Colaig fait (principe 4 du `CLAUDE.md` racine).
        """
        if reaction.emoji not in GESTES_PROPOSES:
            return
        if self._deja_traite(reaction.reaction_id):
            return

        tour = self._messages.get(reaction.message_id)

        try:
            if reaction.emoji == REJOUER:
                await self._rejouer_le_tour(reaction, tour)
            elif reaction.emoji == GARDER:
                await self._garder(reaction, tour)
            elif reaction.emoji in GESTES_DE_JUGEMENT:
                await self._noter(reaction, tour)
        except Exception:
            # Un retour perdu est regrettable ; une boucle de réception morte est une
            # panne. Le canal continue de recevoir.
            logger.exception("geste %s non traité (%s)", reaction.emoji,
                             reaction.reaction_id)

    def _deja_traite(self, reaction_id: str) -> bool:
        if not reaction_id:
            return False
        if reaction_id in self._traites:
            return True
        self._traites[reaction_id] = None
        while len(self._traites) > _MAX_GESTES_RETENUS:
            self._traites.pop(next(iter(self._traites)))
        return False

    # ── Les trois conséquences ──────────────────────────────────────────

    async def _noter(self, reaction: Reaction, tour: Tour | None) -> None:
        """Persiste un jugement — 👍 ou 👎.

        La QUESTION est jointe quand on la connaît encore. Sans elle, « 14 % de 👎 » ne
        dit rien ; avec elle, « 👎 sur les questions de seuil » se corrige.

        Quand la question est perdue, le geste est conservé quand même : ne garder que
        les retours dont on a encore le contexte reviendrait à ne mesurer que le
        quart d'heure qui suit un redémarrage.
        """
        espace = (tour.espace if tour else "") or self._espaces.get(
            reaction.conversation_id, "")
        if not espace:
            # Mode CHATBOT : pas d'espace lié, donc nulle part où écrire. Choisir un
            # dossier par défaut serait exactement ce que D42/D43 refusent.
            logger.debug("retour sans espace connu, non persisté: %s",
                         reaction.conversation_id)
            return

        contenu = {
            "emoji": reaction.emoji,
            "message_id": reaction.message_id,
            "conversation_id": reaction.conversation_id,
            "user_id": reaction.user_id,
            "reaction_id": reaction.reaction_id,
            "horodatage": reaction.horodatage,
            # CE QUI PERMET DE JUGER LE GESTE A FROID.
            #
            # Sans la reponse, un 👎 dit qu'une question a ete mal traitee et rien
            # de plus : le salon est chiffre, on ne peut pas y retourner voir. La
            # donnee etait pourtant la — `_garder` s'en servait deja pour verser
            # la note. Un tour OUBLIE (purge du cache, redemarrage) rend des
            # champs vides et une confiance `None` : mieux vaut un retour
            # incomplet, marque comme tel, que des absences qui ressembleraient a
            # des valeurs mesurees.
            "question": tour.question if tour else "",
            "reponse": tour.reponse if tour else "",
            "sources": list(tour.sources) if tour else [],
            "confiance": tour.confiance if tour else None,
        }
        chemin = paths.feedback_file(espace, _empreinte(reaction.reaction_id))
        await self._storage.upload(
            chemin, json.dumps(contenu, ensure_ascii=False, indent=2).encode("utf-8"))

    async def _garder(self, reaction: Reaction, tour: Tour | None) -> None:
        """Verse la réponse dans `.colaig/notes.md`.

        Rien à garder si le tour est oublié : mieux vaut ne rien écrire qu'une note
        vide, qui occuperait une place et serait indexée pour ne rien dire.
        """
        if tour is None or not tour.espace or not tour.reponse:
            return

        chemin = paths.notes_file(tour.espace)
        ancien = ""
        try:
            if await self._storage.exists(chemin):
                ancien = (await self._storage.download(chemin)).decode("utf-8")
        except Exception:
            # Un ancien contenu illisible ne doit pas empêcher d'écrire la note ; mais
            # il ne doit pas non plus être écrasé en silence.
            logger.warning("notes.md illisible, note non ajoutée: %s", chemin)
            return

        await self._storage.upload(
            chemin, (ancien + _note(reaction, tour)).encode("utf-8"))

    async def _rejouer_le_tour(self, reaction: Reaction, tour: Tour | None) -> None:
        """Relance le tour avec la question d'origine."""
        if tour is None or not tour.question or self._rejouer is None:
            return
        await self._rejouer(reaction, tour.question)


# ── Lecture ─────────────────────────────────────────────────────────────────


async def lire_retours(storage: Any, espace: str) -> list[dict]:
    """Relit les retours d'un espace, du plus ancien au plus récent.

    C'est la face lisible du critère « survit au redémarrage » : rien ici ne dépend du
    processus qui a écrit.
    """
    try:
        fichiers = await storage.list_files(paths.feedback_dir(espace))
    except Exception:
        return []

    retours: list[dict] = []
    for f in fichiers:
        chemin = getattr(f, "path", "") or ""
        if not chemin.endswith(".json"):
            continue
        try:
            retours.append(json.loads(await storage.download(chemin)))
        except Exception:
            logger.warning("retour illisible, ignoré: %s", chemin)

    retours.sort(key=lambda r: (r.get("horodatage", 0), r.get("reaction_id", "")))
    return retours


# ── Helpers ─────────────────────────────────────────────────────────────────


def _empreinte(reaction_id: str) -> str:
    """Condensat servant de nom de fichier.

    Un `event_id` Matrix peut contenir `:` et `$` — illégaux ou piégeux comme nom de
    fichier selon le backend. L'identifiant d'origine est conservé DANS le contenu :
    c'est lui qui dédoublonne un événement redélivré.
    """
    return hashlib.sha256(reaction_id.encode("utf-8")).hexdigest()[:32]


def _note(reaction: Reaction, tour: Tour) -> str:
    """Un bloc Markdown ajouté à la suite des notes existantes."""
    quand = ""
    if reaction.horodatage:
        quand = datetime.fromtimestamp(
            reaction.horodatage / 1000, tz=UTC).strftime("%Y-%m-%d")
    entete = tour.question.strip() or "Note"
    return f"\n## {entete}\n\n{tour.reponse.strip()}\n\n*{quand}*\n"
