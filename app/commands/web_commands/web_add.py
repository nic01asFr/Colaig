import re
from app.services.webdav import WebDAVService, initialize_webdav_service
from app.services.web_classification_browser import extract_page_metadata_with_browser, classify_url_content_with_browser
from app.services.links_db import LinksDatabase

async def add_link_command(matrix_client, room_id, event_id, sender, args, config, context_manager=None, is_silent=False):
    """
    Commande pour ajouter un lien à la base de données de ressources.
    Utilise désormais browser-use pour analyser et classifier automatiquement le contenu.
    
    Args:
        matrix_client: Client Matrix
        room_id: ID du salon
        event_id: ID de l'événement
        sender: Expéditeur de la commande
        args: Arguments de la commande (URL, titre optionnel, catégorie optionnelle)
        config: Configuration
        context_manager: Gestionnaire de contexte
        is_silent: Si True, n'envoie pas de messages de progression
    """
    # Initialisation des variables
    link = None
    title = None
    category = None
    description = None
    manual_category = None
    
    # Vérifier s'il y a des arguments
    if not args:
        if not is_silent:
            await matrix_client.react_to_event(room_id, event_id, "❌")
            await matrix_client.send_markdown_message(
                room_id,
                "Usage: !ajouter_lien <url> [titre] [catégorie] [description]",
                msgtype="m.notice"
            )
        return False
    
    # Initialiser WebDAV
    webdav_service = await initialize_webdav_service(config)
    
    try:
        # Récupérer les arguments
        args_str = " ".join(args)
        # Vérifier si nous avons une URL dans les arguments
        url_match = re.search(r'(https?://\S+)', args_str)
        
        if url_match:
            link = url_match.group(1)
            
            # Message de démarrage
            if not is_silent:
                await matrix_client.react_to_event(room_id, event_id, "🔍")
                await matrix_client.send_markdown_message(
                    room_id, 
                    f"Analyse du lien {link}...",
                    msgtype="m.notice"
                )
            
            # Extraire et analyser le contenu avec browser-use
            try:
                # Extraire toutes les métadonnées en une seule requête
                metadata = await extract_page_metadata_with_browser(config, link)
                
                # Extraire les informations
                if 'title' in metadata:
                    title = metadata['title']
                
                if 'description' in metadata:
                    description = metadata['description']
                    
                # Classifier le contenu
                content = metadata.get('mainContent', '')
                category = await classify_url_content_with_browser(config, link, content)
                
            except Exception as e:
                logger.error(f"Erreur lors de l'extraction avec browser-use: {str(e)}")
                # Extraire le titre de l'URL en cas d'échec
                title = link.split('/')[-1].replace('-', ' ').replace('_', ' ').title()
                
            # Reste des arguments après l'URL
            remaining_args = args_str.replace(link, '', 1).strip()
            
            # Si nous avons encore des arguments, ils peuvent être titre et/ou catégorie
            if remaining_args:
                # Rechercher des catégories connues dans les arguments restants
                categories = ["administration", "technique", "documentation", "formation", "juridique", "général", "actualités"]
                for cat in categories:
                    if cat.lower() in remaining_args.lower():
                        manual_category = cat.lower()
                        remaining_args = remaining_args.replace(cat, '', 1).strip()
                        break
                
                # Si après avoir extrait la catégorie, il reste du texte, c'est le titre
                if remaining_args and not title:
                    title = remaining_args
        else:
            # Pas d'URL trouvée
            if not is_silent:
                await matrix_client.react_to_event(room_id, event_id, "❌")
                await matrix_client.send_markdown_message(
                    room_id,
                    "Aucune URL valide trouvée dans la commande.",
                    msgtype="m.notice"
                )
            return False
        
        # Utiliser la catégorie manuelle si fournie, sinon celle détectée automatiquement
        final_category = manual_category if manual_category else category
        
        # Ajouter le lien à la base
        links_db = LinksDatabase(webdav_service)
        
        result = await links_db.add_link(
            url=link,
            title=title if title else link,
            category=final_category if final_category else "général",
            sender=sender,
            description=description
        )
        
        if result:
            # Message de succès
            if not is_silent:
                await matrix_client.react_to_event(room_id, event_id, "✅")
                
                # Construire le message de confirmation
                confirm_msg = f"Lien ajouté avec succès:\n\n"
                confirm_msg += f"**URL**: {link}\n"
                confirm_msg += f"**Titre**: {title if title else link}\n"
                confirm_msg += f"**Catégorie**: {final_category if final_category else 'général'}\n"
                
                if description:
                    confirm_msg += f"**Description**: {description}\n"
                
                await matrix_client.send_markdown_message(
                    room_id,
                    confirm_msg,
                    msgtype="m.notice"
                )
            return True
        else:
            if not is_silent:
                await matrix_client.react_to_event(room_id, event_id, "❌")
                await matrix_client.send_markdown_message(
                    room_id,
                    "Erreur lors de l'ajout du lien à la base de données.",
                    msgtype="m.notice"
                )
            return False
            
    except Exception as e:
        logger.error(f"Erreur lors de l'ajout du lien: {str(e)}")
        if not is_silent:
            await matrix_client.react_to_event(room_id, event_id, "❌")
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ Erreur lors de l'ajout du lien: {str(e)}",
                msgtype="m.notice"
            )
        return False 