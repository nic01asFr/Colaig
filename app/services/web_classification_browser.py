"""
Service pour la classification automatique des liens web avec browser-use.
Permet d'analyser et de catégoriser automatiquement les liens en utilisant l'automatisation navigateur.
"""

import asyncio
from typing import Dict, Any, Optional

from browser_use import Agent
from app.core_llm import AlbertApiClient
from app.services.browser_extraction import AlbertAgentWrapper

from app.matrix_bot.config import logger


async def classify_url_content_with_browser(config, url, content=None):
    """
    Classifie automatiquement un URL en analysant son contenu avec browser-use.
    
    Args:
        config: Configuration de l'application
        url: URL à classifier
        content: Contenu de l'URL (optionnel, sera récupéré si non fourni)
        
    Returns:
        String: Catégorie du lien (administration, technique, documentation, etc.)
    """
    aclient = None
    
    try:
        # Si le contenu est déjà fourni, l'utiliser directement
        if content:
            chunks = content
        else:
            # Utiliser browser-use pour extraire le contenu
            api_key = config.albert_api_token
            base_url = config.albert_api_url
            model = config.albert_model
            
            # Configurer la tâche d'extraction et de classification
            classification_task = (
                f"Visite l'URL {url} et détermine la catégorie la plus pertinente pour ce contenu. "
                f"Réponds par un seul mot correspondant à la catégorie: "
                f"administration, documentation, technique, information, actualité, outils, autre. "
                f"N'ajoute pas d'explications ou de texte supplémentaire, uniquement la catégorie."
            )
            
            # Créer et configurer l'agent
            agent = Agent(
                task=classification_task,
                llm=AlbertAgentWrapper(api_key=api_key, base_url=base_url, model=model)
            )
            
            # Exécuter l'agent
            chunks = await agent.run()
            
            # Gérer le cas où chunks est un AgentHistoryList
            if not isinstance(chunks, str) and hasattr(chunks, "__class__") and chunks.__class__.__name__ == "AgentHistoryList":
                try:
                    # Essayer d'extraire le contenu textuel des actions de l'agent
                    chunks_str = ""
                    if hasattr(chunks, "steps") and len(chunks.steps) > 0:
                        # Extraire le contenu du dernier résultat d'étape réussi
                        for step in reversed(chunks.steps):
                            if hasattr(step, "result") and step.result:
                                if isinstance(step.result, str) and step.result.strip():
                                    chunks_str = step.result
                                    break
                    
                    # Si aucun contenu valide n'a été trouvé, essayer avec la représentation en chaîne
                    if not chunks_str:
                        chunks_str = str(chunks)
                        
                    logger.info(f"Classification extraite du AgentHistoryList: {chunks_str}")
                    chunks = chunks_str
                except Exception as e:
                    logger.error(f"Erreur lors de l'extraction de la classification du AgentHistoryList: {str(e)}")
                    chunks = "autre"  # Valeur par défaut en cas d'erreur
        
        # Nettoyer et normaliser la classification
        if isinstance(chunks, str):
            # Normaliser la catégorie (convertir en minuscules et supprimer les espaces)
            category = chunks.strip().lower()
            
            # Mapper les synonymes potentiels vers des catégories standardisées
            category_mapping = {
                "admin": "administration",
                "administratif": "administration",
                "gouvernement": "administration",
                "docs": "documentation",
                "manuel": "documentation",
                "guide": "documentation",
                "tech": "technique",
                "développement": "technique",
                "programmation": "technique",
                "infos": "information",
                "ressources": "information",
                "news": "actualité",
                "nouvelles": "actualité",
                "journal": "actualité",
                "outil": "outils",
                "utilitaire": "outils",
                "application": "outils"
            }
            
            # Appliquer le mapping s'il existe, sinon conserver la catégorie
            normalized_category = category_mapping.get(category, category)
            
            # Vérifier que la catégorie fait partie des catégories valides
            valid_categories = ["administration", "documentation", "technique", 
                               "information", "actualité", "outils", "autre"]
            
            if normalized_category not in valid_categories:
                normalized_category = "autre"
                
            return normalized_category
        else:
            # Si chunks n'est pas une chaîne après traitement, retourner "autre"
            return "autre"
            
    except Exception as e:
        logger.error(f"Erreur lors de la classification avec browser-use: {str(e)}")
        return "autre"  # Catégorie par défaut en cas d'erreur
    finally:
        # Fermer le client si nécessaire
        if aclient:
            await aclient.close()


async def get_page_title_with_browser(config, url):
    """
    Extrait le titre d'une page web avec browser-use.
    
    Args:
        config: Configuration de l'application
        url: URL de la page
        
    Returns:
        String: Titre de la page
    """
    try:
        # Utiliser browser-use pour extraire le titre de la page
        api_key = config.albert_api_token
        base_url = config.albert_api_url
        model = config.albert_model
        
        # Configurer la tâche d'extraction du titre
        title_task = f"Visite l'URL {url} et retourne uniquement le titre principal de la page, sans phrases supplémentaires."
        
        # Créer et configurer l'agent
        agent = Agent(
            task=title_task,
            llm=AlbertAgentWrapper(api_key=api_key, base_url=base_url, model=model)
        )
        
        # Exécuter l'agent
        title = await agent.run()
        
        # Gérer le cas où title est un AgentHistoryList
        if not isinstance(title, str) and hasattr(title, "__class__") and title.__class__.__name__ == "AgentHistoryList":
            try:
                # Essayer d'extraire le contenu textuel des actions de l'agent
                title_str = ""
                if hasattr(title, "steps") and len(title.steps) > 0:
                    # Extraire le contenu du dernier résultat d'étape réussi
                    for step in reversed(title.steps):
                        if hasattr(step, "result") and step.result:
                            if isinstance(step.result, str) and step.result.strip():
                                title_str = step.result
                                break
                
                # Si aucun contenu valide n'a été trouvé, essayer avec la représentation en chaîne
                if not title_str:
                    title_str = str(title)
                    
                logger.info(f"Titre extrait du AgentHistoryList: {title_str}")
                title = title_str
            except Exception as e:
                logger.error(f"Erreur lors de l'extraction du titre du AgentHistoryList: {str(e)}")
                # Utiliser le domaine comme titre par défaut
                return url.split("//")[-1].split("/")[0]
        
        # Nettoyer le titre
        return title.strip()
        
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction du titre: {str(e)}")
        # Utiliser le domaine comme titre par défaut
        return url.split("//")[-1].split("/")[0]


async def get_page_description_with_browser(config, url, content=None):
    """
    Génère une description concise d'une page web avec browser-use.
    
    Args:
        config: Configuration de l'application
        url: URL de la page
        content: Contenu de la page (optionnel)
        
    Returns:
        String: Description concise de la page
    """
    aclient = None
    
    try:
        # Si le contenu est déjà fourni, l'utiliser directement
        if content:
            chunks = content
        else:
            # Utiliser browser-use pour extraire le contenu
            api_key = config.albert_api_token
            base_url = config.albert_api_url
            model = config.albert_model
            
            # Configurer la tâche d'extraction
            extraction_task = (
                f"Visite l'URL {url} et extrait le contenu principal du site. "
                f"Ignore les menus, publicités, en-têtes, pieds de page et éléments de navigation. "
                f"Concentre-toi uniquement sur le contenu principal et pertinent. "
                f"Retourne le contenu sous forme de texte brut sans formatage."
            )
            
            # Créer et configurer l'agent
            agent = Agent(
                task=extraction_task,
                llm=AlbertAgentWrapper(api_key=api_key, base_url=base_url, model=model)
            )
            
            # Exécuter l'agent
            chunks = await agent.run()
            
            # Gérer le cas où chunks est un AgentHistoryList
            if not isinstance(chunks, str) and hasattr(chunks, "__class__") and chunks.__class__.__name__ == "AgentHistoryList":
                try:
                    # Essayer d'extraire le contenu textuel des actions de l'agent
                    chunks_str = ""
                    if hasattr(chunks, "steps") and len(chunks.steps) > 0:
                        # Extraire le contenu du dernier résultat d'étape réussi
                        for step in reversed(chunks.steps):
                            if hasattr(step, "result") and step.result:
                                if isinstance(step.result, str) and step.result.strip():
                                    chunks_str = step.result
                                    break
                    
                    # Si aucun contenu valide n'a été trouvé, essayer avec la représentation en chaîne
                    if not chunks_str:
                        chunks_str = str(chunks)
                        
                    logger.info(f"Contenu extrait du AgentHistoryList pour description: {len(chunks_str)} caractères")
                    chunks = chunks_str
                except Exception as e:
                    logger.error(f"Erreur lors de l'extraction du contenu du AgentHistoryList pour description: {str(e)}")
                    chunks = f"Contenu de {url}"  # Valeur par défaut en cas d'erreur
        
        # Initialiser Albert API pour la génération
        api_key = config.albert_api_token
        base_url = config.albert_api_url
        aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
        
        # Tronquer le contenu si nécessaire
        if isinstance(chunks, str) and len(chunks) > 5000:
            chunks = chunks[:5000] + "..."
        
        # Prompt pour générer une description concise
        prompt = f"""Génère une description concise en une seule phrase (15 mots maximum) pour la page web suivante:

{chunks}

Ta description doit capturer l'essence du site et son objectif principal. Réponds uniquement avec la description, sans ajout ni préambule."""
        
        # Générer la description
        description = await aclient.generate(
            model=config.albert_model,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Nettoyer et retourner la description
        return description.strip()
        
    except Exception as e:
        logger.error(f"Erreur lors de la génération de la description: {str(e)}")
        return f"Site web à l'URL {url}"  # Description par défaut en cas d'erreur
    finally:
        # Fermer le client si nécessaire
        if aclient:
            await aclient.close()


async def extract_page_metadata_with_browser(config, url):
    """
    Extrait toutes les métadonnées d'une page web en une seule requête browser-use.
    
    Args:
        config: Configuration de l'application
        url: URL de la page
        
    Returns:
        Dict: Métadonnées de la page (titre, catégorie, description, contenu)
    """
    aclient = None
    
    try:
        # Utiliser browser-use pour extraire le contenu
        api_key = config.albert_api_token
        base_url = config.albert_api_url
        model = config.albert_model
        
        # Configurer la tâche d'extraction complète
        extraction_task = (
            f"Visite l'URL {url} et analyse-la pour me fournir les informations suivantes sous format JSON:\n"
            f"1. title: le titre principal de la page\n"
            f"2. mainContent: le contenu principal du site (ignore menus, publicités, etc.)\n"
            f"3. description: une brève description en une phrase de moins de 15 mots\n"
            f"Retourne uniquement le JSON."
        )
        
        # Créer et configurer l'agent
        agent = Agent(
            task=extraction_task,
            llm=AlbertAgentWrapper(api_key=api_key, base_url=base_url, model=model)
        )
        
        # Exécuter l'agent
        metadata_json = await agent.run()
        
        # Gérer le cas où metadata_json est un AgentHistoryList
        if not isinstance(metadata_json, str) and hasattr(metadata_json, "__class__") and metadata_json.__class__.__name__ == "AgentHistoryList":
            try:
                # Essayer d'extraire le contenu textuel des actions de l'agent
                metadata_str = ""
                if hasattr(metadata_json, "steps") and len(metadata_json.steps) > 0:
                    # Extraire le contenu du dernier résultat d'étape réussi
                    for step in reversed(metadata_json.steps):
                        if hasattr(step, "result") and step.result:
                            if isinstance(step.result, str) and step.result.strip():
                                metadata_str = step.result
                                break
                
                # Si aucun contenu valide n'a été trouvé, essayer avec la représentation en chaîne
                if not metadata_str:
                    metadata_str = str(metadata_json)
                    
                logger.info(f"Métadonnées extraites du AgentHistoryList: {len(metadata_str)} caractères")
                metadata_json = metadata_str
            except Exception as e:
                logger.error(f"Erreur lors de l'extraction des métadonnées du AgentHistoryList: {str(e)}")
                metadata_json = "{}"  # Valeur par défaut en cas d'erreur
        
        # Essayer de parser le résultat comme JSON
        try:
            import json
            metadata = json.loads(metadata_json)
            content = metadata.get("mainContent", "")
        except:
            # Si le parsing échoue, utiliser le résultat brut comme contenu
            logger.warning("Échec du parsing JSON des métadonnées, utilisation du texte brut")
            content = metadata_json
            metadata = {
                "title": url.split("//")[-1].split("/")[0],
                "description": "Page web",
                "mainContent": content
            }
        
        # Générer une catégorie automatiquement
        try:
            aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
            
            # Tronquer le contenu si nécessaire pour la catégorisation
            if len(content) > 5000:
                content_for_category = content[:5000] + "..."
            else:
                content_for_category = content
                
            # Prompt pour la catégorisation
            category_prompt = f"""Analyse ce contenu de page web et détermine la catégorie la plus appropriée parmi: 
administration, documentation, technique, information, actualité, outils, autre.

Contenu:
{content_for_category}

Réponds uniquement avec le nom de la catégorie, sans ajout ni explication."""
            
            # Générer la catégorie
            category = await aclient.generate(
                model=config.albert_model,
                messages=[{"role": "user", "content": category_prompt}]
            )
            
            # Nettoyer et normaliser la catégorie
            category = category.strip().lower()
            
            # Mapping des synonymes
            category_mapping = {
                "admin": "administration",
                "administratif": "administration",
                "docs": "documentation",
                "tech": "technique",
                "infos": "information",
                "news": "actualité",
                "outil": "outils"
            }
            
            # Normaliser la catégorie
            metadata["category"] = category_mapping.get(category, category)
            
        except Exception as e:
            logger.error(f"Erreur lors de la génération de la catégorie: {str(e)}")
            metadata["category"] = "autre"  # Catégorie par défaut
        
        # Ajouter l'URL aux métadonnées
        metadata["url"] = url
        
        return metadata
        
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction des métadonnées: {str(e)}")
        return {
            "title": url.split("//")[-1].split("/")[0],
            "description": "Page web",
            "mainContent": "",
            "category": "autre",
            "url": url
        }
    finally:
        # Fermer le client si nécessaire
        if aclient:
            await aclient.close() 