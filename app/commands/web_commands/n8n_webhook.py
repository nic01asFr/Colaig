"""
Module pour la gestion des webhooks.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import uuid
from pathlib import Path
import hashlib
import secrets

from app.matrix_bot.eventparser import EventParser
from app.matrix_bot.client import MatrixClient
from app.commands.decorators import albert_thread_command, register_feature, thread_response
from app.commands import get_unified_session_context, update_unified_session_context
from app.services.context.models import ConversationStateKeys
from app.services.webhook_service import get_webhook_service, WebhookConfig, WebhookEvent, WebhookResult
from nio import RoomMessageText  # Importer la classe RoomMessageText

logger = logging.getLogger(__name__)

# Pattern pour valider une URL
URL_PATTERN = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+'

# Logs d'initialisation
logger.info("[WEBHOOK_MODULE] Initialisation du module n8n_webhook.py")

@albert_thread_command(
    thread_name="webhook",
    group="web",
    command="webhook",
    help_text="!webhook - Gestion des webhooks pour n8n",
    preserve_context=True,
    timeout=60.0
)
async def webhook_command(ep: EventParser, matrix_client: MatrixClient):
    """
    Gestion des webhooks (intégration avec n8n).
    
    Permet de créer des webhooks sortants (Colaig → n8n) et de gérer les webhooks entrants.
    
    Usage:
        !webhook aide - Affiche l'aide de la commande
        !webhook créer - Crée un webhook sortant
        !webhook liste - Liste les webhooks configurés
        !webhook supprimer <nom> - Supprime un webhook
        !webhook info <nom> - Affiche les détails d'un webhook
        !webhook entrants - Liste les webhooks entrants
    """
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Initialiser le service de webhooks
    webhook_service = await get_webhook_service(config)
    
    # Extraire la commande - en utilisant la structure standard de Colaig
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split(maxsplit=1)
    
    # Si le message est uniquement "!webhook" sans arguments
    if len(command_parts) <= 1:
        sub_command = "aide"
        args = []
    else:
        # Diviser la sous-commande et ses arguments
        parts = command_parts[1].split()
        sub_command = parts[0].lower() if parts else "aide"
        args = parts[1:] if len(parts) > 1 else []
    
    # Log pour le diagnostic
    logger.info(f"[WEBHOOK] Traitement de la sous-commande: '{sub_command}' avec args: {args}")
    
    # Traiter les différentes sous-commandes
    if sub_command in ["aide", "help"]:
        from app.matrix_bot.message_templates import fmt_help
        help_text = fmt_help(
            command="!webhook",
            description="Gestion des webhooks (intégration n8n)",
            usage="!webhook <sous-commande> [arguments]",
            examples=[
                ("!webhook créer", "Crée un webhook sortant (interactif)"),
                ("!webhook liste", "Liste les webhooks configurés"),
                ("!webhook info <nom>", "Détails d'un webhook"),
                ("!webhook supprimer <nom>", "Supprime un webhook"),
                ("!webhook envoyer <nom> {json}", "Envoie des données"),
                ("!webhook entrants", "Liste les webhooks entrants"),
            ],
        )
        await matrix_client.send_markdown_message(
            room_id, help_text, msgtype="m.notice",
        )
        
    elif sub_command in ["créer", "creer", "create"]:
        # Mode conversationnel (commande traditionnelle)
        # Démarrer un thread pour créer un webhook sortant
        thread_data = {
            ConversationStateKeys.ACTION: "create_outgoing",
            ConversationStateKeys.STAGE: "name"
        }
        
        # Mise à jour du contexte de conversation
        session_context = await get_unified_session_context(config, room_id, sender)
        conversation_state = session_context.conversation_state or {}
        
        # Stocker les données initiales dans le contexte
        conversation_state.update(thread_data)
        session_context.conversation_state = conversation_state
        
        # Démarrer le thread de commande
        from app.commands.lifecycle import ThreadedCommandManager
        await ThreadedCommandManager.start_thread(room_id, sender, "webhook", config, **thread_data)
        
        await matrix_client.send_markdown_message(
            room_id,
            "🔗 **Création d'un webhook sortant**\n\n"
            "Indiquez un **nom** pour ce webhook :\n\n"
            "*Annulez à tout moment en répondant `annuler`.*",
            msgtype="m.notice",
        )
        
    elif sub_command in ["liste", "list"]:
        # Récupérer tous les webhooks
        webhooks = await webhook_service.list_webhooks(room_id)
        
        if not webhooks:
            from app.matrix_bot.message_templates import fmt_empty
            await matrix_client.send_markdown_message(
                room_id,
                fmt_empty("webhook", "Créez-en un avec `!webhook créer`."),
                msgtype="m.notice",
            )
            return

        lines = ["🔗 **Webhooks configurés**\n"]
        for name, cfg in webhooks.items():
            lines.append(f"- **{name}** : `{cfg.method}` → {cfg.url}")
        await matrix_client.send_markdown_message(
            room_id, "\n".join(lines), msgtype="m.notice",
        )
        
    elif sub_command in ["supprimer", "delete", "remove"]:
        # Vérifier si le nom du webhook est fourni
        if not args:
            await matrix_client.send_markdown_message(
                room_id,
                "⚠️ Nom du webhook manquant. Usage: `!webhook supprimer <nom>`",
                msgtype="m.notice"
            )
            return
            
        # Récupérer le nom du webhook
        webhook_name = args[0]
        
        # Vérifier si le webhook existe
        webhook_config = await webhook_service.get_webhook_config(webhook_name, room_id)
        if not webhook_config:
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ Webhook **{webhook_name}** non trouvé.",
                msgtype="m.notice"
            )
            return
            
        # Supprimer le webhook
        success = await webhook_service.remove_webhook(webhook_name, room_id)
        
        if success:
            await matrix_client.send_markdown_message(
                room_id,
                f"✅ Webhook **{webhook_name}** supprimé avec succès!",
                msgtype="m.notice"
            )
        else:
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ Échec de la suppression du webhook **{webhook_name}**. Veuillez réessayer.",
                msgtype="m.notice"
            )
            
    elif sub_command in ["info", "details"]:
        # Vérifier si le nom du webhook est fourni
        if not args:
            await matrix_client.send_markdown_message(
                room_id,
                "⚠️ Nom du webhook manquant. Usage: `!webhook info <nom>`",
                msgtype="m.notice"
            )
            return
            
        # Récupérer le nom du webhook
        webhook_name = args[0]
        
        # Vérifier si le webhook existe
        webhook_config = await webhook_service.get_webhook_config(webhook_name, room_id)
        if not webhook_config:
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ Webhook **{webhook_name}** non trouvé.",
                msgtype="m.notice"
            )
            return
            
        # Construire les détails du webhook
        webhook_details = f"""## 🔌 Détails du webhook **{webhook_name}**

- **URL**: {webhook_config.url}
- **Méthode**: {webhook_config.method}
- **Headers**: {len(webhook_config.headers or {})} header(s) configuré(s)
- **Authentification**: {"Configurée" if webhook_config.auth_token else "Non configurée"}
- **Signature**: {"Activée" if webhook_config.secret else "Désactivée"}
- **Timeout**: {webhook_config.timeout} secondes

Pour envoyer des données à ce webhook, utilisez:
```
!webhook envoyer {webhook_name} {"message": "Votre message"}
```"""
        
        await matrix_client.send_markdown_message(
            room_id,
            webhook_details,
            msgtype="m.notice"
        )
        
    elif sub_command in ["entrants", "incoming"]:
        # Récupérer tous les webhooks entrants pour ce salon
        incoming_webhooks = await webhook_service.list_incoming_webhooks(room_id)
        
        if not incoming_webhooks:
            await matrix_client.send_markdown_message(
                room_id,
                "🔍 Aucun webhook entrant configuré pour ce salon.",
                msgtype="m.notice"
            )
            return
            
        # Construire la liste des webhooks entrants
        webhook_list = "## 🔌 Webhooks entrants configurés\n\n"
        for webhook in incoming_webhooks:
            webhook_list += f"- **Token**: {webhook['token']}\n"
            webhook_list += f"  **Description**: {webhook.get('description', 'Non spécifiée')}\n"
            webhook_list += f"  **Créé le**: {webhook.get('created_at', 'Date inconnue')}\n\n"
            
        await matrix_client.send_markdown_message(
            room_id,
            webhook_list,
            msgtype="m.notice"
        )
    
    elif sub_command in ["envoyer", "send", "trigger"]:
        # Vérifier si le nom du webhook est fourni
        if len(args) < 1:
            await matrix_client.send_markdown_message(
                room_id,
                "⚠️ Arguments manquants. Usage: `!webhook envoyer <nom> <données_json>`",
                msgtype="m.notice"
            )
            return
            
        # Récupérer le nom du webhook
        webhook_name = args[0]
        
        # Vérifier si le webhook existe
        webhook_config = await webhook_service.get_webhook_config(webhook_name, room_id)
        if not webhook_config:
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ Webhook **{webhook_name}** non trouvé.",
                msgtype="m.notice"
            )
            return
        
        # Récupérer les données JSON
        if len(args) < 2:
            await matrix_client.send_markdown_message(
                room_id,
                "⚠️ Données JSON manquantes. Usage: `!webhook envoyer <nom> <données_json>`",
                msgtype="m.notice"
            )
            return
            
        # Reconstruire la chaîne JSON (en cas d'espaces dans le JSON)
        json_str = " ".join(args[1:])
        
        # Tenter de corriger les erreurs courantes dans le JSON
        # Si les guillemets sont séparés par des espaces
        json_str = json_str.replace('" ', '"').replace(' "', '"')
        
        try:
            # Parser les données JSON
            payload_data = json.loads(json_str)
            
            # Créer l'événement webhook
            event = WebhookEvent(
                event_type="manual_trigger",
                payload=payload_data
            )
            
            # Envoyer le webhook
            result = await webhook_service.send_webhook(webhook_name, event, room_id)
            
            if result.success:
                # Construire le message de succès
                success_message = f"""✅ Webhook **{webhook_name}** déclenché avec succès!

**Statut**: {result.status_code}
**ID de l'événement**: {event.id}
**Timestamp**: {event.timestamp}

**Réponse**:
```json
{json.dumps(result.response, indent=2, ensure_ascii=False) if result.response else "Aucune réponse"}
```"""
                
                await matrix_client.send_markdown_message(
                    room_id,
                    success_message,
                    msgtype="m.notice"
                )
            else:
                # Construire le message d'erreur
                error_message = f"""❌ Échec du déclenchement du webhook **{webhook_name}**!

**Erreur**: {result.error}
**Statut**: {result.status_code if result.status_code else "N/A"}

**Réponse**:
```json
{json.dumps(result.response, indent=2, ensure_ascii=False) if result.response else "Aucune réponse"}
```"""
                
                await matrix_client.send_markdown_message(
                    room_id,
                    error_message,
                    msgtype="m.notice"
                )
                
        except json.JSONDecodeError as e:
            error_position = str(e).split("(char ")[1].split(")")[0] if "char " in str(e) else "inconnu"
            error_msg = f"⚠️ Format JSON invalide à la position {error_position}.\n\n"
            error_msg += f"JSON reçu: `{json_str}`\n\n"
            error_msg += "Veuillez fournir un objet JSON valide, par exemple:\n"
            error_msg += "```\n{\"message\": \"Votre message\"}\n```"
            
            await matrix_client.send_markdown_message(
                room_id,
                error_msg,
                msgtype="m.notice"
            )
        except Exception as e:
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ Erreur lors de l'envoi du webhook: {str(e)}",
                msgtype="m.notice"
            )
        
    else:
        # Commande non reconnue
        await matrix_client.send_markdown_message(
            room_id,
            f"⚠️ Sous-commande `{sub_command}` non reconnue. Utilisez `!webhook aide` pour voir les commandes disponibles.",
            msgtype="m.notice"
        )

# Fonction de validation pour les réponses webhook
def validate_webhook_response(text):
    """Valide si le texte est une réponse acceptable pour le thread webhook."""
    # La validation est très permissive pour accepter les noms, URLs, etc.
    # On refuse uniquement les chaînes vides
    text = text.strip() if text else ""
    
    # Pour les commandes comme "annuler"
    if text.lower() in ["annuler", "cancel"]:
        return True
    
    # Accepter tout texte non vide qui n'est pas la commande elle-même
    return text and not text.startswith("!webhook")

@register_feature(
    group="web",
    onEvent=RoomMessageText,  # Utiliser la classe au lieu de la chaîne de caractères
    command="",  # Pas de commande spécifique pour les réponses
    help=""  # Pas d'aide pour les réponses
)
@thread_response(command_name="webhook")
async def webhook_response(ep: EventParser, matrix_client: MatrixClient):
    """Traite les réponses utilisateur pour la configuration des webhooks."""
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Ajout de logs détaillés pour le diagnostic
    logger.info(f"[WEBHOOK_RESPONSE] Traitement de la réponse pour le thread webhook")
    logger.info(f"[WEBHOOK_RESPONSE] Sender: {sender}, Room ID: {room_id}")
    
    # Extraire le message
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    logger.info(f"[WEBHOOK_RESPONSE] Message reçu: '{message_text}'")
    
    # Ignorer les commandes webhook
    if message_text.startswith("!webhook"):
        logger.info("[WEBHOOK_RESPONSE] Commande !webhook ignorée dans le handler de réponse")
        return None
    
    # Récupérer le contexte de la conversation
    context = await get_unified_session_context(config, room_id, sender)
    logger.info(f"[WEBHOOK_RESPONSE] État de la conversation: {context}")
    
    # Extraire l'action et l'étape en cours
    action = context.conversation_state.get(ConversationStateKeys.ACTION, "")
    stage = context.conversation_state.get(ConversationStateKeys.STAGE, "")
    logger.info(f"[WEBHOOK_RESPONSE] Action: {action}, Stage: {stage}")
    
    # Initialiser le service webhook
    webhook_service = await get_webhook_service(config)
    
    # Traiter selon l'action et l'étape
    if action == "create_outgoing":
        if stage == "name":
            # Étape 1: Nom du webhook
            webhook_name = message_text.strip()
            
            # Vérifier si le webhook existe déjà
            webhooks = await webhook_service.list_webhooks(room_id)
            if webhook_name in webhooks:
                await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ Un webhook « {webhook_name} » existe déjà. Choisissez un autre nom.",
                    msgtype="m.notice",
                )
                return None

            context.conversation_state[ConversationStateKeys.WEBHOOK_NAME] = webhook_name
            context.conversation_state[ConversationStateKeys.STAGE] = "url"
            await update_unified_session_context(room_id, sender, context)

            await matrix_client.send_markdown_message(
                room_id,
                f"Nom : **{webhook_name}**\n\nIndiquez l'URL du webhook (ex : `https://n8n.example.com/webhook/123`) :",
                msgtype="m.notice",
            )
            return None
            
        elif stage == "url":
            # Étape 2: URL du webhook
            webhook_url = message_text.strip()
            
            if not re.match(URL_PATTERN, webhook_url):
                await matrix_client.send_markdown_message(
                    room_id,
                    "⚠️ URL invalide. Exemple : `https://n8n.example.com/webhook/123`",
                    msgtype="m.notice",
                )
                return None

            context.conversation_state[ConversationStateKeys.WEBHOOK_URL] = webhook_url
            context.conversation_state[ConversationStateKeys.STAGE] = "method"
            await update_unified_session_context(room_id, sender, context)

            await matrix_client.send_markdown_message(
                room_id,
                f"URL : **{webhook_url}**\n\nMéthode HTTP (POST, GET, PUT…) ou Entrée pour POST :",
                msgtype="m.notice",
            )
            return None
            
        elif stage == "method":
            # Étape 3: Méthode HTTP
            webhook_method = message_text.strip().upper() if message_text.strip() else "POST"
            
            # Récupérer les informations du webhook
            webhook_name = context.conversation_state.get(ConversationStateKeys.WEBHOOK_NAME, "")
            webhook_url = context.conversation_state.get(ConversationStateKeys.WEBHOOK_URL, "")
            
            # Créer le webhook
            webhook_config = WebhookConfig(
                url=webhook_url,
                method=webhook_method,
                headers={}
                # Suppression de created_at qui n'est pas dans la classe WebhookConfig
            )
            
            await webhook_service.add_webhook(webhook_name, webhook_config, room_id)

            await matrix_client.send_markdown_message(
                room_id,
                f"✅ Webhook **{webhook_name}** créé.\n\n"
                f"- Envoyer : `!webhook envoyer {webhook_name} {{\"message\": \"…\"}}`\n"
                f"- Détails : `!webhook info {webhook_name}`\n"
                f"- Supprimer : `!webhook supprimer {webhook_name}`\n\n"
                f"→ `{webhook_method}` {webhook_url}",
                msgtype="m.notice",
            )
            
            # Terminer le thread de commande
            context.conversation_state[ConversationStateKeys.COMMAND_COMPLETED] = True
            context.conversation_state[ConversationStateKeys.LAST_ACTION] = "création_terminée"
            context.conversation_state[ConversationStateKeys.ACTION] = "création_terminée"
            context.conversation_state[ConversationStateKeys.STATUS] = "success"
            context.conversation_state[ConversationStateKeys.WEBHOOK_METHOD] = webhook_method
            
            # Fermer explicitement le thread
            from app.commands.lifecycle import ThreadedCommandManager
            await ThreadedCommandManager.end_thread(room_id, sender, "webhook", config, 
                                                  action="création_terminée",
                                                  status="success",
                                                  webhook_name=webhook_name,
                                                  webhook_url=webhook_url,
                                                  webhook_method=webhook_method)
            
            return "Webhook créé avec succès"
        else:
            logger.warning(f"[WEBHOOK_RESPONSE] Action {action}, étape {stage} non reconnue")
    else:
        logger.warning(f"[WEBHOOK_RESPONSE] Action '{action}' non reconnue")
    
    return None 