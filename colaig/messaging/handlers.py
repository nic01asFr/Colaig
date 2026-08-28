"""
Colaig — Handlers de messages (provider-agnostic)

Pipeline Phase 1 : Message → Resolver → Retriever → Generator → Réponse.
Pipeline Phase 2 : Message → Resolver → Analyser → Orchestrateur → Synthétiseur → Réponse.

Détection automatique : si les 3 agents sont fournis → Phase 2.
Phase 5 : conversation_memory pour chargement sémantique de l'historique.
Toute exception → message d'erreur user-friendly dans la conversation + log complet.

Utilise uniquement les Protocols (injection dans le constructeur).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from colaig.integrations.albert import AlbertClient

from colaig.context.layers import save_conversation_history
from colaig.messaging.progress import ProgressReporter, resolve_channel
from colaig.models import ContextMode, IncomingMessage, IntentType, PipelinePhase

logger = logging.getLogger(__name__)


# Ce que Colaig sait ranger. Les IMAGES en font partie : un plan photographié, un devis
# scanné, un panneau relevé sur le terrain sont des documents, et l'OCR existe (L1.3b).
#
# La VIDÉO n'y est pas : Colaig ne sait pas la lire, et l'accepter promettrait un
# traitement qui n'existe pas.
_TYPES_DOCUMENT = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument",   # docx, xlsx, pptx
    "application/msword",
    "application/vnd.oasis.opendocument",              # odt, ods
    "text/",
    "image/",
)


def est_audio(piece) -> bool:
    """Cette pièce jointe est-elle une parole à transcrire ?"""
    return (piece.content_type or "").startswith("audio/")


def est_document(piece) -> bool:
    """Cette pièce jointe est-elle un document à ranger ?

    LE TYPE MIME FAIT FOI, PAS LE NOM. Un fichier nommé `rapport.pdf` mais annoncé
    `video/mp4` n'est pas un PDF : se fier à l'extension, c'est se fier à ce que
    l'expéditeur a bien voulu écrire.

    UN TYPE INCONNU OU ABSENT N'EST PAS UN DOCUMENT. C'est le sens sûr : mieux vaut ne
    pas ranger un fichier qu'on ne sait pas lire que l'écrire dans l'espace sur une
    supposition.

    SANS CONTENU, RIEN À RANGER. Le téléchargement peut échouer sans que la pièce
    disparaisse du message ; écrire un document vide serait pire que ne rien faire — il
    occuperait une place, serait indexé, et répondrait du vide à une question.
    """
    if not piece.content:
        return False
    mime = (piece.content_type or "").lower()
    if not mime or est_audio(piece):
        return False
    return any(mime.startswith(prefixe) for prefixe in _TYPES_DOCUMENT)

# Message d'erreur envoyé à l'utilisateur en cas de problème
ERROR_MESSAGE = (
    "Désolé, je rencontre un problème technique. "
    "Réessaie dans quelques instants ou contacte l'administrateur."
)

# Message d'onboarding pour les salons non liés à un workspace
_ONBOARDING_MESSAGE = """\
Bonjour ! Je suis **Colaig**, votre assistant documentaire IA.

Ce salon n'est pas encore lié à un espace de travail. \
Pour m'activer et accéder à vos documents :

**Option 1 — Commande directe dans ce salon**
```
colaig créer <nom de l'espace>
```
*Exemple :* `colaig créer Équipe RH` — crée un espace et lie ce salon immédiatement.

**Option 2 — Via un client MCP (Claude Desktop, etc.)**
Utilisez l'outil `colaig_onboard` pour une configuration guidée complète \
(storage, LLM, workspace, canal).
Ou directement : `colaig_create_workspace` / `colaig_link_conversation`.

**Option 3 — Via l'API admin**
```
POST /workspaces
{
  "storage_path": "/mon-dossier/",
  "name": "Mon équipe",
  "conversations": ["{conversation_id}"]
}
```

**Option 4 — Manuellement**
Déposez un fichier `.colaig/config.yaml` dans votre dossier sur le storage \
avec `conversations: ["{conversation_id}"]`.

Une fois configuré, je pourrai : rechercher dans vos documents, \
mémoriser le contexte de vos échanges, et lancer des tâches autonomes.\
"""

# Commandes d'auto-configuration détectées dans les messages
_CMD_CREATE = ("colaig créer", "colaig create", "colaig init", "/colaig init")
_CMD_LINK = ("colaig lier", "colaig link", "/colaig link")


class MessageHandler:
    """Handler de messages — orchestre le pipeline Phase 1 ou Phase 2.

    Toutes les dépendances sont injectées via le constructeur (Protocol-only).
    Si analyser, orchestrator, et synthesiser sont fournis → Phase 2 automatique.

    Args:
        messaging: Canal de communication (MessagingProtocol).
        resolver: Résolveur de contexte (ContextResolverProtocol).
        retriever: Service de recherche RAG (RetrieverProtocol).
        generator: Générateur de réponses Phase 1 (GeneratorProtocol).
        storage: Backend de stockage pour sauvegarder l'historique (StorageProtocol).
        analyser: Agent Analyseur Phase 2 (optionnel).
        orchestrator: Agent Orchestrateur Phase 2 (optionnel).
        synthesiser: Agent Synthétiseur Phase 2 (optionnel).
        conversation_memory: Mémoire conversationnelle sémantique (Phase 5, optionnel).
        tool_registry: Registre d'outils (Phase 5, optionnel, pour info uniquement ici).
        workspace_stores: Dict workspace_id → FaissStore pour isolation des requêtes par workspace.
        on_phase_change: Callback optionnel notifié à chaque changement de phase.
    """

    def __init__(
        self,
        messaging,   # MessagingProtocol
        resolver,    # ContextResolverProtocol
        retriever,   # RetrieverProtocol
        generator,   # GeneratorProtocol
        storage,     # StorageProtocol
        analyser=None,           # AnalyserProtocol (Phase 2)
        orchestrator=None,       # OrchestratorProtocol (Phase 2)
        synthesiser=None,        # SynthesiserProtocol (Phase 2)
        conversation_memory=None,  # ConversationMemory (Phase 5, optionnel)
        tool_registry=None,        # ToolRegistry (Phase 5, optionnel)
        workspace_stores=None,     # dict[workspace_id, FaissStore] — isolation par workspace
        workspace_bm25_stores=None,  # dict[workspace_id, BM25Store] — hybrid search
        on_phase_change: Callable[[PipelinePhase, str], Awaitable[None]] | None = None,
        albert_client: AlbertClient | None = None,
        trame_manager=None,        # TrameManager (Phase 6, optionnel)
        pre_exec_builder=None,     # PreExecutionBuilder (Phase 6, optionnel)
        user_memory=None,          # UserMemory (Phase 7, optionnel) — mémoire per-user
    ) -> None:
        self._messaging = messaging
        self._resolver = resolver
        self._retriever = retriever
        self._generator = generator
        self._storage = storage
        self._analyser = analyser
        self._orchestrator = orchestrator
        self._synthesiser = synthesiser
        self._conversation_memory = conversation_memory
        self._tool_registry = tool_registry
        self._workspace_stores = workspace_stores      # dict[str, FaissStore] | None
        self._workspace_bm25_stores = workspace_bm25_stores  # dict[str, BM25Store] | None
        self._on_phase_change = on_phase_change
        self._albert_client = albert_client
        self._trame_manager = trame_manager
        self._pre_exec_builder = pre_exec_builder
        self._user_memory = user_memory

    @property
    def is_phase2(self) -> bool:
        """True si les 3 agents Phase 2 sont disponibles."""
        return all([self._analyser, self._orchestrator, self._synthesiser])

    async def process(
        self,
        body: str,
        conversation_id: str = "api-test",
        user_id: str = "api-user",
    ) -> str:
        """Exécute le pipeline complet et retourne le texte de la réponse.

        Crée un handler temporaire avec un adaptateur de capture en mémoire —
        tous les composants (resolver, agents, storage…) sont partagés avec self.

        Args:
            body: Corps du message à traiter.
            conversation_id: ID de la conversation (doit être lié à un workspace
                pour que le RAG soit activé, sinon mode CHATBOT).
            user_id: ID de l'utilisateur émetteur.

        Returns:
            Texte de la réponse, ou chaîne vide si aucune réponse envoyée.
        """
        captured: list[str] = []

        class _ResponseCapture:
            async def send(self, conv_id, text, **kwargs):
                captured.append(text)

            async def send_typing(self, conv_id, **kwargs):
                pass

        temp_handler = MessageHandler(
            messaging=_ResponseCapture(),
            resolver=self._resolver,
            retriever=self._retriever,
            generator=self._generator,
            storage=self._storage,
            analyser=self._analyser,
            orchestrator=self._orchestrator,
            synthesiser=self._synthesiser,
            conversation_memory=self._conversation_memory,
            tool_registry=self._tool_registry,
            workspace_stores=self._workspace_stores,
            albert_client=self._albert_client,
        )

        msg = IncomingMessage(
            conversation_id=conversation_id,
            user_id=user_id,
            body=body,
            is_reply=True,
        )
        await temp_handler.handle_message(msg)
        return captured[0] if captured else ""

    async def handle_message(self, message: IncomingMessage) -> None:
        """Dispatch vers le pipeline Phase 1 ou Phase 2.

        Avant tout traitement :
        1. Transcrit les pièces jointes audio (via Albert Whisper) si présentes.
        2. Vérifie si le salon est en mode CHATBOT et intercepte les commandes d'onboarding.
        Toute erreur lors de la pré-résolution est traitée par le pipeline.
        """
        # AIGUILLAGE DES PIÈCES JOINTES (L3.7).
        #
        # Cette branche disait « pièce jointe + corps vide = message vocal ». C'était
        # vrai tant que Matrix ne délivrait QUE de l'audio — quatre rappels enregistrés,
        # aucun pour les fichiers. En ouvrant la réception aux documents, l'équivalence
        # devient fausse, et déposer un PDF répondrait « Je n'arrive pas à traiter ce
        # message vocal » : l'assistant nommerait une chose que personne n'a faite.
        #
        # L'audio passe D'ABORD sur un message mixte : c'est une PAROLE, elle porte une
        # intention et attend une réponse, tandis que le document attend un rangement.
        if (message.attachments and not message.body.strip()
                and not any(est_audio(p) for p in message.attachments)
                and any(est_document(p) for p in message.attachments)):
            await self._ranger_documents(message)
            return

        # Transcription audio : si message vocal sans texte → transcrire d'abord
        if message.attachments and not message.body.strip():
            await self._transcribe_audio(message)
            if not message.body.strip():
                # Pas de transcription possible (audio vide, service indisponible…)
                try:
                    await self._messaging.send(
                        message.conversation_id,
                        "Je n'arrive pas à traiter ce message vocal. Envoie-moi un message texte.",
                    )
                except Exception:
                    logger.exception("impossible d'envoyer la réponse audio échoué")
                return

        # Pré-résolution légère pour détecter le mode (onboarding uniquement)
        try:
            pre_context = await self._resolver.resolve(message)
        except Exception:
            # Erreur de résolution → laisser le pipeline gérer (enverra ERROR_MESSAGE)
            if self.is_phase2:
                await self._handle_phase2(message)
            else:
                await self._handle_phase1(message)
            return

        if pre_context.mode == ContextMode.CHATBOT:
            # Essayer d'intercepter une commande d'auto-configuration
            handled = await self._handle_onboarding_command(message)
            if handled:
                return
            # Sinon : première fois dans ce salon → envoyer le message d'onboarding
            if not message.is_reply:
                await self._send_onboarding(message)
                return

        # ── Reprise d'un appel destructif suspendu (L2.4b) ─────────────────────
        #
        # AVANT tout le reste : si une action attend un accord dans cette conversation,
        # le message courant est une reponse a cette question, pas une nouvelle demande.
        #
        # La reconnaissance est MECANIQUE — aucun modele ne decide de ce qui vaut
        # confirmation, sinon une consigne deposee dans un document fabriquerait la
        # sienne.
        if await self._reprendre_confirmation(message):
            return

        # ── Détection tâche waiting_for_user (Mode C human-in-the-loop) ─────────
        # Si c'est un DM en mode PERSONAL et qu'une tâche attend une réponse :
        # injecter la réponse et remettre la tâche pending (le scheduler reprendra).
        if pre_context.mode == ContextMode.PERSONAL and self._storage:
            handled = await self._handle_waiting_task_reply(message)
            if handled:
                return

        if self.is_phase2:
            await self._handle_phase2(message, context=pre_context)
        else:
            await self._handle_phase1(message, context=pre_context)

    async def _reprendre_confirmation(self, message: IncomingMessage) -> bool:
        """Traite le message comme une reponse a une confirmation en attente.

        Returns:
            True si le message a ete consomme — il ne doit pas descendre dans le
            pipeline, sinon un « oui » relancerait une analyse d'intention sur le mot
            « oui ».
        """
        from colaig.security.confirmation import (
            ANNULE,
            CONFIRME,
            attentes_en_cours,
            lire_reponse,
        )

        attentes = attentes_en_cours()
        if not attentes.en_attente(message.conversation_id):
            return False

        verdict = lire_reponse(message.body)

        if verdict == CONFIRME:
            attente = attentes.reprendre(message.conversation_id)
            if attente is None:
                # Expiree entre la question et la reponse. On ne l'execute pas : une
                # confirmation qu'on peut donner le lendemain n'en est plus une.
                await self._messaging.send(
                    message.conversation_id,
                    "La demande a expire. Reformulez-la si elle est toujours d'actualite.",
                )
                return True
            logger.info(
                "confirmation accordee : %s dans %s",
                attente.outil, message.conversation_id,
            )
            # L'accord est enregistre pour le PROCHAIN appel de cet outil — a usage
            # unique, borne au salon et dans le temps.
            #
            # Rejouer l'appel ici serait plus direct, mais le tour interactif ne sait pas
            # reprendre un appel d'outil isole hors de la boucle agentique. Faire
            # reformuler est moins elegant et parfaitement honnete : l'action ne
            # s'execute qu'apres un accord explicite, et l'accord ne vaut qu'une fois.
            # TODO-NORMALE : rejouer l'appel directement quand la boucle saura reprendre.
            attentes.accorder(message.conversation_id, attente.outil)
            await self._messaging.send(
                message.conversation_id,
                f"Accord enregistre pour `{attente.outil}`. "
                "Reformulez votre demande : elle sera executee sans nouvelle question.",
            )
            return True

        attentes.oublier(message.conversation_id)
        if verdict == ANNULE:
            # Ni oui ni non : le doute ne vaut pas accord. On abandonne l'attente ET on
            # laisse le message suivre son cours — c'est peut-etre une vraie question.
            logger.info("confirmation abandonnee (reponse non concluante) dans %s",
                        message.conversation_id)
            return False

        await self._messaging.send(message.conversation_id, "Action abandonnee.")
        return True

    async def _handle_waiting_task_reply(self, message: IncomingMessage) -> bool:
        """Injecte une réponse DM dans une tâche waiting_for_user si applicable.

        Cherche dans le workspace personnel de l'utilisateur une tâche avec
        status="waiting_for_user". Si trouvée, écrit la réponse dans
        session.pending_user_reply et repasse task.status="pending".

        Returns:
            True si une tâche a été reprise (le message ne doit pas être traité
            normalement), False sinon.
        """
        try:
            from colaig.agents.tasks import (
                list_tasks,
                load_session_state,
                save_session_state,
                save_task,
            )
            from colaig.context.workspace import get_or_create_personal_workspace

            personal_ws = await get_or_create_personal_workspace(self._storage, message.user_id)
            tasks = await list_tasks(self._storage, personal_ws.storage_path)

            waiting = [t for t in tasks if t.status == "waiting_for_user" and t.enabled]
            if not waiting:
                return False

            # Prendre la tâche la plus récente en attente
            task = waiting[0]

            # Vérifier que le message vient bien du propriétaire de la tâche
            if message.user_id != task.user_id:
                logger.warning(
                    "handlers: réponse à tâche en attente ignorée — user_id mismatch "
                    "(task=%s, sender=%s)",
                    task.user_id,
                    message.user_id,
                )
                return False

            # Écrire la réponse dans session.json
            session = await load_session_state(self._storage, task)
            if session is not None:
                session.pending_user_reply = message.body
                session.status = "running"
                await save_session_state(self._storage, task, session)

            # Remettre la tâche en pending → le scheduler reprendra
            task.status = "pending"
            await save_task(self._storage, task)

            # Accuser réception à l'utilisateur
            try:
                await self._messaging.send(
                    message.conversation_id,
                    f"✅ Réponse reçue pour la tâche **{task.name}**. "
                    "La session reprend automatiquement.",
                )
            except Exception:
                pass

            logger.info(
                "handle_message: réponse injectée dans tâche %s (waiting_for_user → pending)",
                task.task_id,
            )
            return True

        except Exception as exc:
            logger.warning("_handle_waiting_task_reply: erreur: %s", exc)
            return False

    # =========================================================================
    # Audio — transcription des messages vocaux
    # =========================================================================

    async def _ranger_documents(self, message: IncomingMessage) -> None:
        """Range les documents déposés dans l'espace, et le dit.

        LE CLASSEMENT N'EST PAS RÉÉCRIT ICI. `ClassificationEngine` existe, est branché
        dans `document_index.py`, et lit ses règles dans `.colaig/rules/`. Ce chemin ne
        fait que **déposer** le fichier dans l'espace documentaire ; l'indexation
        incrémentale le prendra et le classera comme n'importe quel autre document.

        Réécrire un second chemin de classement produirait deux règles divergentes —
        ce dépôt en a mesuré le coût neuf fois.

        LE NOM EST VALIDÉ. Un nom de fichier vient de l'extérieur : il peut contenir une
        traversée (`../`) ou viser `.colaig/`, ce qui ferait écrire dans le dossier
        d'instance depuis un simple dépôt en salon. C'est la même garde que pour les
        cibles de livraison (L2.6).

        ON DIT CE QU'ON A FAIT. Un dépôt silencieux laisse l'utilisateur ignorer si le
        document est arrivé — et il le redéposera.
        """
        from colaig.security.path_validator import validate_storage_path

        try:
            contexte = await self._resolver.resolve(message)
            espace = getattr(getattr(contexte, "workspace", None), "storage_path", "")
        except Exception:
            espace = ""
        if not espace:
            logger.warning("document déposé hors espace connu (%s)", message.conversation_id)
            await self._dire(message, "Je ne sais pas dans quel espace ranger ce "
                                      "document : ce salon n'est rattaché à aucun dossier.")
            return

        ranges, refuses = [], []
        for piece in message.attachments:
            if not est_document(piece):
                continue
            try:
                cible = validate_storage_path(
                    f"{espace.rstrip('/')}/{piece.filename}",
                    allow_dotcolaig=False, context="dépôt en salon")
                await self._storage.upload(cible, piece.content)
                ranges.append(piece.filename)
            except Exception as exc:
                logger.warning("dépôt refusé pour %s : %s", piece.filename, exc)
                refuses.append(piece.filename)

        if ranges:
            await self._dire(message, "Document déposé dans l'espace : "
                                      + ", ".join(f"**{n}**" for n in ranges)
                                      + ". Il sera indexé et classé à la prochaine "
                                        "analyse.")
        if refuses:
            await self._dire(message, "Je n'ai pas pu ranger : "
                                      + ", ".join(refuses) + ".")

    async def _dire(self, message: IncomingMessage, texte: str) -> None:
        """Envoie une réponse sans laisser une panne d'envoi casser la réception."""
        try:
            await self._messaging.send(message.conversation_id, texte)
        except Exception:
            logger.exception("envoi impossible dans %s", message.conversation_id)

    async def _transcribe_audio(self, message: IncomingMessage) -> None:
        """Transcrit les pièces jointes audio et injecte le texte dans message.body.

        Parcourt les attachments en cherchant les audio/* avec des bytes disponibles.
        Met à jour message.body si la transcription réussit.
        Silencieux en cas d'erreur (pas de client Albert, API indisponible…).
        """
        if self._albert_client is None:
            return

        for att in message.attachments:
            if not att.content:
                continue
            if not att.content_type.startswith("audio/"):
                continue
            try:
                text = await self._albert_client.transcribe(att.content, att.filename)
                if text.strip():
                    message.body = text.strip()
                    logger.info(
                        "vocal transcrit (%d chars) user=%s: %r",
                        len(text), message.user_id, text[:100],
                    )
                    return
            except Exception:
                logger.warning("transcription audio échouée pour %s", att.filename, exc_info=True)

    # =========================================================================
    # Onboarding — salons non liés à un workspace
    # =========================================================================

    async def _send_onboarding(self, message: IncomingMessage) -> None:
        """Envoie le message d'onboarding dans un salon inconnu."""
        try:
            text = _ONBOARDING_MESSAGE.replace("{conversation_id}", message.conversation_id)
            await self._messaging.send(message.conversation_id, text)
            logger.info("onboarding envoyé: conversation=%s", message.conversation_id)
        except Exception:
            logger.exception("impossible d'envoyer l'onboarding: %s", message.conversation_id)

    async def _handle_onboarding_command(self, message: IncomingMessage) -> bool:
        """Intercepte les commandes d'auto-configuration dans les salons inconnus.

        Commandes supportées :
        - "colaig créer <nom>" / "colaig create <name>"
            → crée un workspace et lie ce salon
        - "colaig lier <workspace_id>" / "colaig link <workspace_id>"
            → lie ce salon à un workspace existant

        Returns:
            True si une commande a été interceptée et traitée, False sinon.
        """
        body = message.body.strip()
        body_lower = body.lower()

        # ── commande "créer workspace" ─────────────────────────────────
        for prefix in _CMD_CREATE:
            if body_lower.startswith(prefix):
                name = body[len(prefix):].strip() or "Nouveau workspace"
                await self._messaging.send_typing(message.conversation_id, typing=True)
                try:
                    from colaig.context.workspace import create_workspace
                    # Le créateur est inscrit PROPRIÉTAIRE.
                    #
                    # Sans cela, l'espace naît sans personne pour l'administrer : son
                    # créateur ne pourrait pas y rattacher un second salon, la garde de
                    # `can_link_conversation` le traitant comme un inconnu. Un espace
                    # orphelin dès sa création n'est pas un espace.
                    ws = await create_workspace(
                        storage=self._storage,
                        storage_path=f"/{_slugify_msg(name)}/",
                        name=name,
                        conversations=[message.conversation_id],
                        owners=[message.user_id] if message.user_id else None,
                    )
                    await self._resolver.register_workspace(ws)
                    await self._messaging.send(
                        message.conversation_id,
                        f"✅ Workspace **{ws.name}** créé (`{ws.workspace_id}`).\n"
                        f"Ce salon est maintenant lié. Déposez vos documents dans "
                        f"`{ws.storage_path}` pour que je puisse les indexer.",
                    )
                    logger.info(
                        "onboarding: workspace créé via commande: %s → %s",
                        message.conversation_id, ws.workspace_id,
                    )
                except Exception as e:
                    await self._messaging.send(
                        message.conversation_id,
                        f"❌ Impossible de créer le workspace : {e}",
                    )
                finally:
                    await self._messaging.send_typing(message.conversation_id, typing=False)
                return True

        # ── commande "lier workspace" ──────────────────────────────────
        for prefix in _CMD_LINK:
            if body_lower.startswith(prefix):
                from colaig.security.acl import WorkspaceACL

                workspace_id = body[len(prefix):].strip()
                if not workspace_id:
                    # N'énumérer QUE les espaces où le demandeur est admis.
                    #
                    # La liste des espaces d'une instance est en soi une information :
                    # elle nomme les équipes, les directions, parfois les dossiers en
                    # cours. Elle était rendue à quiconque savait inviter le bot.
                    visibles = [
                        ws for ws in self._resolver.workspaces
                        if WorkspaceACL.can_link_conversation(ws, message.user_id)
                    ]
                    await self._messaging.send(
                        message.conversation_id,
                        "Usage : `colaig lier <workspace_id>`\n"
                        + ("Espaces auxquels vous êtes déclaré : "
                           + ", ".join(f"`{ws.workspace_id}`" for ws in visibles)
                           if visibles else
                           "Vous n'êtes déclaré sur aucun espace existant. "
                           "Utilisez `colaig créer <nom>` pour ouvrir le vôtre."),
                    )
                    return True

                await self._messaging.send_typing(message.conversation_id, typing=True)
                try:
                    from colaig.context.workspace import add_conversation_to_workspace
                    target = next(
                        (ws for ws in self._resolver.workspaces
                         if ws.workspace_id == workspace_id),
                        None,
                    )
                    if target is None:
                        await self._messaging.send(
                            message.conversation_id,
                            f"❌ Workspace `{workspace_id}` introuvable.",
                        )
                        return True

                    # LA GARDE QUI COMPTE — l'appariement salon → espace EST la
                    # frontière d'accès du chemin conversationnel. Une fois le salon
                    # rattaché, tout ce qui s'y dit interroge le corpus de l'espace,
                    # sans autre contrôle : `WorkspaceACL` garde les outils
                    # d'administration, la délégation et les tâches, jamais ce chemin.
                    #
                    # Sans cette garde, deux messages depuis n'importe quel salon
                    # suffisaient à lire le corpus de n'importe quel espace. Mesuré.
                    #
                    # Le refus ne distingue pas « introuvable » de « interdit » à
                    # dessein : distinguer redonnerait par la porte l'énumération qu'on
                    # vient de fermer par la fenêtre.
                    if not WorkspaceACL.can_link_conversation(target, message.user_id):
                        logger.warning(
                            "rattachement refusé : %s n'est pas déclaré sur %s",
                            message.user_id, target.workspace_id,
                        )
                        await self._messaging.send(
                            message.conversation_id,
                            f"❌ Workspace `{workspace_id}` introuvable ou non "
                            "accessible. Demandez à son administrateur de vous "
                            "déclarer, ou créez le vôtre avec `colaig créer <nom>`.",
                        )
                        return True

                    updated_ws = await add_conversation_to_workspace(
                        self._storage, target.storage_path, message.conversation_id
                    )
                    await self._resolver.register_workspace(updated_ws)
                    await self._messaging.send(
                        message.conversation_id,
                        f"✅ Ce salon est maintenant lié au workspace **{updated_ws.name}** "
                        f"(`{updated_ws.workspace_id}`). "
                        f"Posez-moi vos questions sur les documents de cet espace !",
                    )
                    logger.info(
                        "onboarding: liaison via commande: %s → %s",
                        message.conversation_id, workspace_id,
                    )
                except Exception as e:
                    await self._messaging.send(
                        message.conversation_id,
                        f"❌ Impossible de lier le salon : {e}",
                    )
                finally:
                    await self._messaging.send_typing(message.conversation_id, typing=False)
                return True

        return False

    # =========================================================================
    # Pipeline Phase 1 (inchangé)
    # =========================================================================

    async def _handle_phase1(self, message: IncomingMessage, context=None) -> None:
        """Pipeline Phase 1 complet.

        1. Typing ON
        2. Résoudre contexte (workspace, mode, system_prompt)
        3. Si workspace avec RAG → rechercher
        4. Générer réponse (1 appel Albert)
        5. Envoyer réponse dans la conversation
        6. Typing OFF + sauvegarder historique
        """
        try:
            # 1. Typing ON
            await self._messaging.send_typing(message.conversation_id, typing=True)

            # 2. Résoudre le contexte (réutilise la pré-résolution de handle_message si fournie)
            if context is None:
                context = await self._resolver.resolve(message)
            channel_format = resolve_channel(message.platform or "", message.conversation_type)

            # 3. Recherche RAG si activé
            search_results = []
            if context.workspace and context.workspace.rag_enabled:
                retrieve_kwargs: dict = dict(
                    query=message.body,
                    k=context.workspace.max_results,
                    score_threshold=context.workspace.similarity_threshold,
                )
                # Isolation par workspace : utiliser le store spécifique si disponible
                if self._workspace_stores:
                    ws_store = self._workspace_stores.get(context.workspace.workspace_id)
                    if ws_store is not None:
                        retrieve_kwargs["store"] = ws_store
                # Hybrid search : index BM25 par workspace
                if self._workspace_bm25_stores:
                    ws_bm25 = self._workspace_bm25_stores.get(context.workspace.workspace_id)
                    if ws_bm25 is not None:
                        retrieve_kwargs["bm25_store"] = ws_bm25
                search_results = await self._retriever.retrieve(**retrieve_kwargs)

            # 4. Générer la réponse (1 seul appel Albert)
            response = await self._generator.generate(
                query=message.body,
                context=context,
                search_results=search_results,
                conversation_history=context.conversation_history,
                channel_format=channel_format,
            )

            # 5. Envoyer la réponse
            await self._messaging.send(message.conversation_id, response.text)

            # Log échange complet pour suivi et amélioration de la pertinence
            logger.info(
                "échange workspace=%s user=%r question=%r sources=%s confiance=%.2f temps_ms=%d",
                context.workspace.workspace_id if context.workspace else "_chatbot",
                message.display_name or message.user_id,
                message.body[:80],
                response.sources,
                response.confidence,
                response.generation_time_ms,
            )

            # 6. Sauvegarder l'historique
            await self._save_history(message, response.text, context)

        except Exception:
            logger.exception("erreur pipeline pour message %s", message.message_id)
            try:
                await self._messaging.send(message.conversation_id, ERROR_MESSAGE)
            except Exception:
                logger.exception("impossible d'envoyer le message d'erreur")

        finally:
            try:
                await self._messaging.send_typing(message.conversation_id, typing=False)
            except Exception:
                pass

    # =========================================================================
    # Pipeline Phase 2 (Analyseur → Orchestrateur → Synthétiseur)
    # =========================================================================

    async def _handle_phase2(self, message: IncomingMessage, context=None) -> None:
        """Pipeline Phase 2 complet.

        1. Typing ON
        2. Résoudre contexte
        3. THINKING — Analyser le message → Intent avec directives
        4. RETRIEVING/EXECUTING — Orchestrer → Plan avec résultats
        5. SYNTHESIZING — Synthétiser → Réponse finale
        6. COMPLETE — Envoyer + sauvegarder
        """
        try:
            # 1. Typing ON
            await self._messaging.send_typing(message.conversation_id, typing=True)

            # 2. Résoudre le contexte (réutilise la pré-résolution de handle_message si fournie)
            if context is None:
                context = await self._resolver.resolve(message)
            channel_format = resolve_channel(message.platform or "", message.conversation_type)
            reporter = ProgressReporter(self._messaging, message.conversation_id, channel_format)

            # 2b. Charger l'historique via ConversationMemory si disponible (Phase 5)
            if self._conversation_memory and context.workspace and context.workspace.storage_path:
                try:
                    history = await self._conversation_memory.load_relevant_history(
                        workspace_path=context.workspace.storage_path,
                        conversation_id=message.conversation_id,
                        current_query=message.body,
                    )
                    context.conversation_history = history
                except Exception:
                    logger.warning("impossible de charger l'historique via ConversationMemory")

            # 2c. Charger la trame vivante (Phase 6)
            trame = None
            if self._trame_manager and context.workspace and context.workspace.storage_path:
                try:
                    trame = await self._trame_manager.load(
                        message.conversation_id, context.workspace.storage_path
                    )
                    context.conversation_phase = trame.conversation_phase
                    context.context_anchors = list(trame.context_anchors)
                except Exception:
                    logger.warning("trame non chargée conv=%s", message.conversation_id)

            # 2d. Construire PreExecutionCard (Phase 6)
            pre_exec = None
            if self._pre_exec_builder and trame and context.workspace:
                try:
                    pre_exec = await self._pre_exec_builder.build(message, trame, context)
                    logger.debug(
                        "meta_rag: behavior=%s score=%.2f skills=%d",
                        pre_exec.active_behavior_name or "none",
                        pre_exec.active_behavior_score,
                        len(pre_exec.selected_skills),
                    )
                except Exception:
                    logger.warning("pre_exec build échoué — dégradation gracieuse")

            # 2e. User memory — lecture sans Phase 6
            # Si pre_exec déjà construit par pre_exec_builder, user_memory déjà inclus.
            # Sinon, on lit les faits directement pour les injecter dans l'Analyseur.
            if (
                pre_exec is None
                and self._user_memory
                and self._albert_client
                and context.workspace
                and context.workspace.storage_path
            ):
                try:
                    msg_emb = await self._albert_client.embed(message.body)
                    facts = await self._user_memory.read(
                        user_id=message.user_id,
                        workspace_path=context.workspace.storage_path,
                        message_embedding=msg_emb,
                        k=5,
                    )
                    if facts:
                        from colaig.models import PreExecutionCard
                        pre_exec = PreExecutionCard(
                            workspace_id=context.workspace.workspace_id,
                            conversation_phase=None,
                            fixed_context={"user_memory": [f.content for f in facts]},
                        )
                        logger.debug("user_memory: %d faits injectés (sans Phase 6)", len(facts))
                except Exception:
                    logger.warning("user_memory: lecture sans Phase 6 échouée — dégradation gracieuse")

            # 3. THINKING — Analyse
            await self._notify_phase(PipelinePhase.THINKING, message.conversation_id)
            await reporter.report("thinking")
            intent = await self._analyser.analyse(message, context, pre_exec=pre_exec)

            logger.info(
                "analyse: type=%s, needs_rag=%s, confiance=%.2f",
                intent.intent_type.value, intent.needs_rag, intent.confidence,
            )

            # Fast-path salutation : vider l'historique pour éviter que le synthétiseur
            # génère une réponse documentaire par contamination de l'historique précédent.
            if intent.intent_type == IntentType.GREETING and not intent.needs_rag:
                context.conversation_history = []

            # 4. RETRIEVING/EXECUTING — Orchestration
            await self._notify_phase(PipelinePhase.RETRIEVING, message.conversation_id)
            await reporter.report("retrieving")
            plan = await self._orchestrator.execute(
                intent, context, pre_exec=pre_exec, reporter=reporter
            )

            _content_rag_count = len(plan.search_results) + sum(
                len(r.get("results", [])) for r in plan.tool_results
                if r.get("tool") == "search_documents"
            )
            logger.info(
                "orchestration: steps=%d, results=%d, temps=%dms",
                len(plan.steps), _content_rag_count, plan.execution_time_ms,
            )

            # 5. SYNTHESIZING — Synthèse
            await self._notify_phase(PipelinePhase.SYNTHESIZING, message.conversation_id)
            await reporter.report("synthesizing")
            response = await self._synthesiser.synthesise(
                plan, context, context.conversation_history, channel_format,
                pre_exec=pre_exec, message=message,
            )

            # 6. COMPLETE — Envoi
            await self._notify_phase(PipelinePhase.COMPLETE, message.conversation_id)
            await self._messaging.send(message.conversation_id, response.text)
            if not response.sources and intent.needs_rag:
                await self._messaging.send(
                    message.conversation_id,
                    "_Aucun document pertinent trouvé dans cet espace. "
                    "Cette réponse est basée sur mes connaissances générales._",
                    is_status=True,
                )

            logger.info(
                "échange workspace=%s user=%r question=%r sources=%s confiance=%.2f temps_ms=%d",
                context.workspace.workspace_id if context.workspace else "_chatbot",
                message.display_name or message.user_id,
                message.body[:80],
                response.sources,
                response.confidence,
                response.generation_time_ms,
            )

            # Mettre à jour et persister la trame vivante (Phase 6)
            if trame and self._trame_manager and context.workspace and context.workspace.storage_path:
                try:
                    trame = await self._trame_manager.update(trame, intent, plan, response)
                    readonly = getattr(context.workspace, "storage_readonly", False)
                    await self._trame_manager.save(
                        trame, context.workspace.storage_path, storage_readonly=readonly
                    )
                    logger.debug("trame sauvegardée conv=%s", message.conversation_id)
                except Exception:
                    logger.warning("trame non sauvegardée conv=%s", message.conversation_id)

            # Sauvegarder l'historique (via ConversationMemory si disponible)
            if self._conversation_memory and context.workspace and context.workspace.storage_path:
                try:
                    await self._conversation_memory.save_turn(
                        workspace_path=context.workspace.storage_path,
                        conversation_id=message.conversation_id,
                        user_message=message.body,
                        assistant_response=response.text,
                        existing_history=context.conversation_history,
                    )
                except Exception:
                    logger.exception("impossible de sauvegarder via ConversationMemory")
            else:
                await self._save_history(message, response.text, context)

            # UserMemory — extraction fire-and-forget (ne bloque pas la réponse)
            if self._user_memory and message.user_id:
                workspace_path = context.workspace.storage_path if context.workspace else ""
                if workspace_path:
                    self._user_memory.schedule_extract(
                        user_id=message.user_id,
                        workspace_path=workspace_path,
                        user_msg=message.body,
                        assistant_msg=response.text,
                        conversation_id=message.conversation_id,
                    )
                else:
                    logger.debug("user_memory: workspace_path vide pour %s, extraction ignorée", message.user_id)

        except Exception:
            logger.exception("erreur pipeline Phase 2 pour message %s", message.message_id)
            try:
                await self._messaging.send(message.conversation_id, ERROR_MESSAGE)
            except Exception:
                logger.exception("impossible d'envoyer le message d'erreur")

        finally:
            try:
                await self._messaging.send_typing(message.conversation_id, typing=False)
            except Exception:
                pass

    # =========================================================================
    # Utilitaires
    # =========================================================================

    async def _notify_phase(self, phase: PipelinePhase, conversation_id: str) -> None:
        """Notifie le callback de changement de phase."""
        if self._on_phase_change:
            try:
                await self._on_phase_change(phase, conversation_id)
            except Exception:
                pass

    async def _save_history(self, message: IncomingMessage, response_text: str, context) -> None:
        """Sauvegarde le tour de conversation dans l'historique."""
        if not context.workspace or not context.workspace.storage_path:
            return

        history = list(context.conversation_history)
        history.append({"role": "user", "content": message.body})
        history.append({"role": "assistant", "content": response_text})

        try:
            await save_conversation_history(
                self._storage,
                context.workspace.storage_path,
                message.conversation_id,
                history,
            )
        except Exception:
            logger.exception("impossible de sauvegarder l'historique: %s", message.conversation_id)


# =============================================================================
# Helpers module-level
# =============================================================================

def _slugify_msg(text: str) -> str:
    """Transforme un nom en slug pour les workspace créés via commande."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r"[àáâãäå]", "a", slug)
    slug = re.sub(r"[èéêë]", "e", slug)
    slug = re.sub(r"[ìíîï]", "i", slug)
    slug = re.sub(r"[òóôõö]", "o", slug)
    slug = re.sub(r"[ùúûü]", "u", slug)
    slug = re.sub(r"[ç]", "c", slug)
    slug = re.sub(r"[^a-z0-9_\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "workspace"
