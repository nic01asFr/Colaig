"""
Colaig — un outil destructif attend une confirmation explicite.

STATUT: COMPLET
VERSION: 2026-08-25 - v1.0
LOT: L2.4b

Le canal
--------
Confirmation **par réponse texte**, sur le modèle de `_handle_waiting_task_reply` qui
existe déjà pour les tâches de fond. La confirmation par réaction ✅ exigeait d'étendre
`MessagingProtocol`, donc de toucher `protocols.py` — arbitrage écarté (D47).

La reconnaissance est mécanique, et c'est le point central
------------------------------------------------------------
Si un modèle décidait ce qui vaut confirmation, une consigne déposée dans un document
pourrait produire la sienne — et l'on aurait bâti une porte dont l'attaquant tient la
clé. **Aucun modèle n'intervient ici.**

La comparaison porte sur le message **entier**, normalisé, contre une liste courte.
Jamais une sous-chaîne : « surtout pas oui » contient « oui », et « non merci » contient
« non ». Une garde qui lit des sous-chaînes se retourne contre elle-même.

Tout ce qui n'est ni oui ni non **annule** l'attente. Le doute ne vaut pas accord, et
sans cela un « oui » adressé à autre chose, trois messages plus tard, déclencherait
l'action oubliée.

Où vit l'attente, et pourquoi en mémoire
------------------------------------------
En mémoire du processus, pas dans `.colaig/`. Une attente rangée dans l'espace serait
modifiable par qui y écrit : l'utilisateur confirmerait « crée le document X » et l'appel
repris serait un autre — la confirmation deviendrait un blanc-seing.

Le prix est qu'un redémarrage perd les attentes en cours. C'est un échec dans le bon
sens : rien ne s'exécute, l'utilisateur redemande.

Ce que ce module ne fait pas
-----------------------------
Il ne dit pas quels outils sont destructifs — c'est `security/actions.py` (L2.4a). Et il
ne garantit pas que le modèle ne trouvera pas un chemin détourné : c'est ce que la suite
adversariale de L2.5 devra mesurer.
"""
from __future__ import annotations

import json
import logging
import time
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CONFIRME = "confirme"
REFUSE = "refuse"
ANNULE = "annule"

# Listes COURTES et closes. Les élargir augmente la surface d'une confirmation
# accidentelle ; chaque ajout doit se justifier par un usage observé, pas par
# anticipation.
_OUI = frozenset({"oui", "confirme", "ok", "valide", "vas-y", "d'accord", "yes"})
_NON = frozenset({"non", "annule", "stop", "surtout pas", "no"})

DELAI_PAR_DEFAUT = 300


def _normaliser(texte: str) -> str:
    """Minuscules, accents retirés, ponctuation de fin ôtée, espaces réduits.

    On normalise pour ne pas refuser « Oui. » ou « confirmé », **pas** pour élargir la
    reconnaissance à autre chose que les formes listées.
    """
    plat = unicodedata.normalize("NFD", (texte or "").strip().lower())
    plat = "".join(c for c in plat if unicodedata.category(c) != "Mn")
    return " ".join(plat.strip(" .!…").split())


def lire_reponse(texte: str) -> str:
    """`CONFIRME`, `REFUSE`, ou `ANNULE` pour tout le reste.

    La comparaison porte sur le message ENTIER. Une correspondance par sous-chaîne
    ferait de « surtout pas oui » une confirmation.
    """
    plat = _normaliser(texte)
    if plat in _OUI:
        return CONFIRME
    if plat in _NON:
        return REFUSE
    return ANNULE


@dataclass
class Attente:
    """Un appel d'outil suspendu, en attente de l'accord de l'utilisateur."""

    outil: str
    arguments: dict = field(default_factory=dict)
    posee_a: float = 0.0


class Attentes:
    """Les confirmations en cours, une par conversation au plus.

    Une seule par conversation : deux actions suspendues rendraient « oui » ambigu, et
    une confirmation ambiguë ne confirme rien.
    """

    def __init__(self, horloge=time.monotonic, delai: int = DELAI_PAR_DEFAUT) -> None:
        # L'horloge est injectée pour que le harnais reste déterministe — aucune horloge
        # murale dans les tests (`tests/CLAUDE.md`).
        self._horloge = horloge
        self._delai = delai
        self._par_conversation: dict[str, Attente] = {}
        self._accords: dict[tuple[str, str], float] = {}

    def poser(self, conversation_id: str, outil: str, arguments: dict) -> str:
        """Suspend l'appel et rend la question à poser à l'utilisateur.

        La question **nomme l'outil et ses arguments** : confirmer à l'aveugle ne
        confirme rien.
        """
        self._par_conversation[conversation_id] = Attente(
            outil=outil, arguments=dict(arguments or {}), posee_a=self._horloge(),
        )
        logger.info("confirmation demandée pour %s dans %s", outil, conversation_id)

        detail = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)
        return (
            f"⚠️ Cette action modifie quelque chose et attend votre accord.\n\n"
            f"**Action** : `{outil}`\n"
            f"**Paramètres** : `{detail}`\n\n"
            f"Répondez **oui** pour l'exécuter, **non** pour l'abandonner. "
            f"Toute autre réponse annule la demande."
        )

    def reprendre(self, conversation_id: str) -> Attente | None:
        """L'appel suspendu de cette conversation, s'il est encore valable.

        La reprise **consomme** l'attente : sans cela un second « oui » rejouerait la
        même action.
        """
        attente = self._par_conversation.pop(conversation_id, None)
        if attente is None:
            return None
        if self._horloge() - attente.posee_a > self._delai:
            logger.info(
                "confirmation expirée pour %s dans %s — l'action n'est pas exécutée",
                attente.outil, conversation_id,
            )
            return None
        return attente

    def oublier(self, conversation_id: str) -> None:
        """Abandonne l'attente — sur refus, ou sur toute réponse qui n'en est pas une."""
        self._par_conversation.pop(conversation_id, None)

    def en_attente(self, conversation_id: str) -> bool:
        return conversation_id in self._par_conversation

    # ── L'accord accorde ────────────────────────────────────────────────────
    #
    # Sans lui, l'utilisateur boucle : il confirme, reformule, l'orchestrateur suspend
    # a nouveau, il reconfirme. Un outil destructif deviendrait inutilisable — ce qui
    # casserait le Mode C et les outils d'administration.
    #
    # L'accord est A USAGE UNIQUE, borne a un outil, a un salon, et dans le temps. Un
    # accord permanent serait un blanc-seing, pas une confirmation.

    def accorder(self, conversation_id: str, outil: str) -> None:
        """Enregistre l'accord donne, pour le prochain appel de cet outil."""
        self._accords[(conversation_id, outil)] = self._horloge()
        logger.info("accord enregistre : %s dans %s", outil, conversation_id)

    def consommer_accord(self, conversation_id: str, outil: str) -> bool:
        """Cet appel a-t-il ete autorise ? L'accord est consomme au passage."""
        donne_a = self._accords.pop((conversation_id, outil), None)
        if donne_a is None:
            return False
        if self._horloge() - donne_a > self._delai:
            logger.info("accord expire : %s dans %s", outil, conversation_id)
            return False
        return True


_attentes_du_processus: Attentes | None = None


def attentes_en_cours() -> Attentes:
    """Les attentes de ce processus — partagees par l'orchestrateur et le gestionnaire.

    Un singleton, parce que les deux extremites du cycle vivent dans des modules
    differents : l'orchestrateur SUSPEND au milieu d'un tour, le gestionnaire REPREND au
    message suivant. Les faire dialoguer par une instance injectee demanderait de la
    porter a travers tout le pipeline pour un etat qui ne survit pas au redemarrage.

    En memoire, deliberement : une attente rangee dans `.colaig/` serait modifiable par
    qui ecrit dans l'espace, et la confirmation deviendrait un blanc-seing.
    """
    global _attentes_du_processus
    if _attentes_du_processus is None:
        _attentes_du_processus = Attentes()
    return _attentes_du_processus


def reinitialiser_les_attentes() -> None:
    """Vide le singleton — reserve aux tests, qui doivent partir d'un etat connu."""
    global _attentes_du_processus
    _attentes_du_processus = None
