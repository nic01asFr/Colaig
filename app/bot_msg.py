from app.config import APP_VERSION, COMMAND_PREFIX, Config


class AlbertMsg:
    common_msg_prefixes = [
        "👋 Bonjour, je suis **Colaig Albert**",
        "🤖 Configuration actuelle",
        "\u26a0\ufe0f **Erreur**",
        "\u26a0\ufe0f **Commande inconnue**",
        "**La conversation a été remise à zéro**",
        "🤖 Albert a échoué",
    ]
    shorts = {
        "help": f"Pour retrouver ce message informatif, tapez `{COMMAND_PREFIX}aide`. Pour les geek tapez `{COMMAND_PREFIX}aide -v`.",
        "reset": f"Pour ré-initialiser notre conversation, tapez `{COMMAND_PREFIX}reset`",
        "collections": f"Pour modifier l'ensemble des collections utilisées quand vous me posez une question, tapez `{COMMAND_PREFIX}collections list/use/unuse/info COLLECTION_NAME/{Config().albert_all_public_command}`",
        "conversation": f"Pour activer/désactiver le mode conversation, tapez `{COMMAND_PREFIX}conversation`",
        "debug": f"Pour afficher des informations sur la configuration actuelle, `{COMMAND_PREFIX}debug`",
        "model": f"Pour modifier le modèle, tapez `{COMMAND_PREFIX}model MODEL_NAME`",
        "mode": f"Pour modifier le mode du modèle (c'est-à-dire le modèle de prompt utilisé), tapez `{COMMAND_PREFIX}mode MODE`",
        "sources": f"Pour obtenir les sources utilisées pour générer ma dernière réponse, tapez `{COMMAND_PREFIX}sources`",
        "recherche_web": f"Pour rechercher des informations sur Internet, tapez `{COMMAND_PREFIX}recherche_web [votre question]`",
        "ajouter_lien": f"Pour ajouter un lien à la base de données, tapez `{COMMAND_PREFIX}ajouter_lien [URL] [titre?] [catégorie?]`",
        "liste_liens": f"Pour afficher la liste des liens enregistrés, tapez `{COMMAND_PREFIX}liste_liens [catégorie?]`",
        "explorer_lien": f"Pour explorer et résumer le contenu d'un lien, tapez `{COMMAND_PREFIX}explorer_lien [URL]`",
    }

    failed = "🤖 Albert a échoué à répondre. Veuillez réessayez dans un moment."

    flush_start = "Nettoyage des collections RAG propres à cette conversation..."

    flush_end = "Nettoyage des collections RAG terminé."

    reset = "**La conversation a été remise à zéro**. Vous pouvez néanmoins toujours répondre dans un fil de discussion."

    user_not_allowed = "Albert est en phase de test et n'est pas encore disponible pour votre utilisateur. Contactez albert-contact@data.gouv.fr pour demander un accès."

    domain_not_allowed = "Albert n'est pas encore disponible pour votre domaine. Merci de rester en contact, il sera disponible après une phase beta test."

    def error_debug(reason, config):
        msg = f"\u26a0\ufe0f **Albert API error**\n\n{reason}\n\n- Albert API URL: {config.albert_api_url}\n- Matrix server: {config.matrix_home_server}"
        return msg

    def help(cls, cmds=None, verbose=False):
        """
        Generate help message
        """
        if cmds is None:
            cmds = []

        if verbose:
            msg = "🤖 Je suis Albert, votre assistant IA pour les tâches collaboratives.\n\n"
            msg += "Je peux vous aider à explorer des documents, répondre à vos questions et même rechercher des informations sur Internet.\n\n"
            msg += "📖 **Règles d'utilisation**\n"
            msg += "1️⃣ Posez-moi des questions de façon claire et précise\n"
            msg += "2️⃣ Je peux chercher dans les documents que vous me partagez\n"
            msg += "3️⃣ Je peux rechercher des informations sur Internet avec la commande !recherche_web\n"
            msg += "4️⃣ Je peux vous aider à explorer des liens web avec !explorer_lien\n"
            msg += "5️⃣ Pour voir toutes les commandes disponibles, tapez !aide\n\n"
            msg += "📁 **Sur l'usage des données**\nLes conversations sont stockées sur votre espace documentaire. Elles me permettent de contextualiser les conversations et l'équipe qui me développe les utilise pour m'évaluer et analyser mes performances.\n\n"
            msg += "📯 Nous contacter : colaig.assistant@developpement-durable.gouv.fr"

            msg += "\n\n**Commandes disponibles :**\n"
            msg += "- " + "\n- ".join(cmds)
            
            msg += "\n\n**Commandes web :**\n"
            msg += "!recherche_web [question] - Rechercher des informations sur Internet\n"
            msg += "!explorer_lien [URL] - Explorer et résumer le contenu d'un lien\n"
            msg += "!ajouter_lien [URL] - Ajouter un lien à la base de données\n"
            msg += "!liste_liens - Afficher les liens enregistrés\n"

            msg += "\n**Commandes avancées :**\n"
            msg += "!source - Sélectionner les sources à utiliser pour la conversation"
        else:
            msg = "🤖 Je suis Albert, votre assistant IA pour les tâches collaboratives.\n\n"
            msg += "**Commandes principales :**\n"
            for cmd in cmds:
                msg += f"- {cmd}\n"
                
            msg += "\n**Commandes web :**\n"
            msg += "!recherche_web [question] - Rechercher des informations sur Internet\n"
            msg += "!explorer_lien [URL] - Explorer et résumer le contenu d'un lien\n"
            msg += "!ajouter_lien [URL] - Ajouter un lien à la base de données\n"
            msg += "!liste_liens - Afficher les liens enregistrés\n"
                
            msg += "\nPour plus d'informations, tapez `!aide verbose`"

        return msg

    def commands(cmds):
        msg = "Les commandes spéciales suivantes sont disponibles :\n\n"
        msg += "- " + "\n- ".join(cmds)  # type: ignore
        return msg

    def unknown_command(cmds_msg):
        msg = f"\u26a0\ufe0f **Commande inconnue**\n\n{cmds_msg}"
        return msg

    def reset_notif(delay_min):
        msg = f"Comme vous n'avez pas continué votre conversation avec Albert depuis plus de {delay_min} minutes, **la conversation a été automatiquement remise à zéro**. Vous pouvez néanmoins toujours répondre dans un fil de discussion.\n\n"
        msg += "Entrez **!aide** pour obtenir plus d'informatin sur ma paramétrisatiion."
        return msg

    def debug(config: Config):
        msg = "🤖 Configuration actuelle :\n\n"
        msg += f"- Version: {APP_VERSION}\n"
        msg += f"- API: {config.albert_api_url}\n"
        msg += f"- Model: {config.albert_model}\n"
        msg += f"- Mode: {config.albert_mode}\n"
        msg += f"- With history: {config.albert_with_history}\n"
        return msg
