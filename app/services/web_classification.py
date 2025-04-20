"""
Service pour la classification automatique des liens web.
Permet d'analyser et de catégoriser automatiquement les liens en fonction de leur contenu.
"""

import aiohttp
from app.core_llm import AlbertApiClient
from app.matrix_bot.config import logger

async def classify_url_content(config, url, content=None):
    """
    Classifie automatiquement un URL en analysant son contenu.
    
    Args:
        config: Configuration de l'application
        url: URL à classifier
        content: Contenu de l'URL (optionnel, sera récupéré si non fourni)
        
    Returns:
        String: Catégorie du lien (administration, technique, documentation, etc.)
    """
    
    api_key = config.albert_api_token
    base_url = config.albert_api_url
    aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
    
    try:
        # Si le contenu n'est pas fourni, obtenir d'abord le contenu de l'URL
        if not content:
            # Utiliser la collection internet pour récupérer le contenu
            session = aiohttp.ClientSession()
            data = {"collections": ["internet"], "k": 4, "prompt": f"Contenu de {url}"}
            response = await session.post(url=f"{base_url}/v1/search", json=data)
            search_results = await response.json()
            
            # Extraire le contenu
            chunks = "\n\n".join([result["chunk"]["content"] for result in search_results["data"]])
            await session.close()
        else:
            chunks = content
        
        # Prompt pour classification
        classification_prompt = f"""
Analyse le contenu suivant extrait du site {url} et détermine la catégorie 
la plus appropriée parmi les suivantes:
- administration
- technique
- documentation
- formation
- juridique
- général
- actualités

Réponds uniquement par le nom de la catégorie en minuscule.
"""
        
        # Obtenir la classification
        category = await aclient.generate(
            model=config.albert_model,
            messages=[{"role": "user", "content": classification_prompt}]
        )
        
        # Nettoyer le résultat
        category = category.strip().lower()
        
        # Valider la catégorie
        valid_categories = ["administration", "technique", "documentation", 
                           "formation", "juridique", "général", "actualités", "general"]
        
        if category not in valid_categories:
            category = "general"  # Catégorie par défaut
            
        # Remplacer "général" par "general" pour cohérence
        if category == "général":
            category = "general"
        if category == "actualités":
            category = "actualites"
            
        return category
        
    except Exception as e:
        logger.error(f"Erreur lors de la classification de l'URL: {str(e)}")
        return "general"  # Catégorie par défaut en cas d'erreur
    finally:
        if 'aclient' in locals():
            await aclient.close()

async def get_page_title(config, url):
    """
    Extrait le titre d'une page web.
    
    Args:
        config: Configuration de l'application
        url: URL de la page
        
    Returns:
        String: Titre de la page
    """
    api_key = config.albert_api_token
    base_url = config.albert_api_url
    aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
    
    try:
        title_prompt = f"Donne le titre exact du site web {url}. Réponds uniquement avec le titre, sans phrases."
        title = await aclient.generate(
            model=config.albert_model,
            messages=[{"role": "user", "content": title_prompt}]
        )
        return title.strip()
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction du titre: {str(e)}")
        # Utiliser le domaine comme titre par défaut
        return url.split("//")[-1].split("/")[0]
    finally:
        if 'aclient' in locals():
            await aclient.close()

async def get_page_description(config, url, content=None):
    """
    Génère une description concise d'une page web.
    
    Args:
        config: Configuration de l'application
        url: URL de la page
        content: Contenu de la page (optionnel)
        
    Returns:
        String: Description concise de la page
    """
    api_key = config.albert_api_token
    base_url = config.albert_api_url
    aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
    
    try:
        # Si le contenu n'est pas fourni, obtenir d'abord le contenu de l'URL
        if not content:
            # Utiliser la collection internet pour récupérer le contenu
            session = aiohttp.ClientSession()
            data = {"collections": ["internet"], "k": 3, "prompt": f"Contenu de {url}"}
            response = await session.post(url=f"{base_url}/v1/search", json=data)
            search_results = await response.json()
            
            # Extraire le contenu
            chunks = "\n\n".join([result["chunk"]["content"] for result in search_results["data"]])
            await session.close()
        else:
            chunks = content
        
        description_prompt = f"""
À partir du contenu suivant extrait du site {url}, génère une description concise en 
une phrase de 15 mots maximum.

Contenu:
{chunks}

Réponds uniquement avec la description, sans introduction ni phrase supplémentaire.
"""
        
        description = await aclient.generate(
            model=config.albert_model,
            messages=[{"role": "user", "content": description_prompt}]
        )
        
        return description.strip()
    except Exception as e:
        logger.error(f"Erreur lors de la génération de la description: {str(e)}")
        return "Page web"
    finally:
        if 'aclient' in locals():
            await aclient.close() 