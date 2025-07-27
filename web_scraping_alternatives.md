# Alternatives à l'API d'Albert pour les requêtes Web

## Outils existants dans le projet

D'après l'analyse du code et des dépendances, les bibliothèques suivantes sont déjà disponibles dans le projet et peuvent être utilisées pour le scraping web:

- **aiohttp**: Client HTTP asynchrone 
- **httpx**: Client HTTP moderne avec support HTTP/2
- **requests**: Client HTTP synchrone populaire
- **pdfminer.six**: Extraction de texte depuis des PDFs
- **BeautifulSoup4**: Non listé mais probablement disponible pour parser le HTML

## Solutions recommandées

### 1. Solution simple avec HTTPX (recommandée)

HTTPX est déjà une dépendance du projet et offre une API moderne avec support asynchrone:

```python
import httpx
import asyncio
from bs4 import BeautifulSoup

async def fetch_and_parse_url(url):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            
            # Obtenir le contenu HTML
            html_content = response.text
            
            # Parser avec BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extraire le texte en supprimant le contenu non pertinent
            # Supprimer les scripts, styles, etc.
            for script in soup(["script", "style", "header", "footer", "nav"]):
                script.extract()
                
            # Obtenir le texte
            text = soup.get_text(separator='\n')
            
            # Nettoyer le texte (espaces multiples, lignes vides)
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)
            
            # Extraire le titre
            title = soup.title.string if soup.title else ""
            
            return {
                "title": title,
                "content": text,
                "url": url,
                "status": response.status_code
            }
            
        except httpx.HTTPStatusError as e:
            return {
                "error": f"Erreur HTTP: {e.response.status_code}",
                "url": url,
                "status": e.response.status_code
            }
        except Exception as e:
            return {
                "error": f"Erreur: {str(e)}",
                "url": url,
                "status": 0
            }
```

### 2. Solution avec Trafilatura (à installer)

Trafilatura est une bibliothèque spécialisée dans l'extraction de contenu textuel de qualité à partir de pages web:

```python
# Nécessite: pip install trafilatura
import trafilatura
import httpx
import asyncio

async def fetch_and_extract_with_trafilatura(url):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            
            # Obtenir le contenu HTML
            html_content = response.text
            
            # Utiliser trafilatura pour extraire le contenu principal
            extracted_text = trafilatura.extract(html_content, include_comments=False, 
                                                include_tables=True, no_fallback=False)
            
            # Extraire les métadonnées
            metadata = trafilatura.metadata.extract_metadata(html_content, url=url)
            
            title = metadata.title if metadata and metadata.title else ""
            
            return {
                "title": title,
                "content": extracted_text,
                "url": url,
                "status": response.status_code
            }
            
        except Exception as e:
            return {
                "error": f"Erreur: {str(e)}",
                "url": url,
                "status": 0
            }
```

### 3. Solution avec newspaper3k (à installer)

newspaper3k est spécialisé dans l'extraction d'articles de presse et de blogs:

```python
# Nécessite: pip install newspaper3k
import newspaper
import httpx
import asyncio

async def fetch_with_newspaper(url):
    try:
        # newspaper n'est pas asynchrone, donc utiliser run_in_executor
        article = await asyncio.to_thread(newspaper.Article, url)
        await asyncio.to_thread(article.download)
        await asyncio.to_thread(article.parse)
        
        return {
            "title": article.title,
            "content": article.text,
            "summary": article.summary,
            "keywords": article.keywords,
            "url": url,
            "status": 200
        }
    except Exception as e:
        return {
            "error": f"Erreur: {str(e)}",
            "url": url,
            "status": 0
        }
```

## Implémentation dans `explore_link_command`

Pour remplacer l'API d'Albert dans la fonction `explore_link_command`, voici une implémentation:

```python
async def explore_link_command(ep: EventParser, matrix_client: MatrixClient):
    """Explore et résume le contenu d'un lien."""
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    try:
        # Extraire l'URL
        message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
        args = message_text.split(maxsplit=1)
        
        # Vérifier les arguments
        if len(args) <= 1:
            await matrix_client.send_markdown_message(
                room_id,
                """❓ **Comment utiliser !explorer_lien**

```
!explorer_lien URL
```

J'explorerai et résumerai le contenu de cette page web.""",
                msgtype="m.notice"
            )
            return
        
        url = args[1]
        
        # Valider l'URL
        if not url.startswith(("http://", "https://")):
            await matrix_client.send_markdown_message(
                room_id,
                "⚠️ L'URL doit commencer par http:// ou https://",
                msgtype="m.notice"
            )
            return
        
        # Message de chargement
        await matrix_client.send_markdown_message(
            room_id,
            f"🔍 Exploration de {url}...",
            msgtype="m.notice"
        )
        
        # Rechercher d'abord dans le cache
        web_search_cache = await get_web_search_cache(config)
        cache_key = f"explorer:{url}"
        cached_result = await web_search_cache.get(cache_key, "explorer")
        
        if cached_result:
            # Utiliser le résultat en cache
            summary = cached_result.get("summary", "")
            response = f"**Résumé de [{url}]({url})**\n\n{summary}"
            
            # Enregistrer la visite
            web_links_manager = await get_web_links_manager(config)
            await web_links_manager.increment_visit(url)
            
            # Proposer d'ajouter le lien à la base de données s'il n'y est pas déjà
            all_links = await web_links_manager.get_all_links()
            link_exists = any(link["url"] == url for link in all_links)
            
            if not link_exists:
                response += "\n\n---\n\nCe lien n'est pas dans la base de données. Pour l'ajouter :\n"
                response += f"`!ajouter_lien {url}`"
            
            # Envoyer la réponse
            await matrix_client.send_markdown_message(
                room_id,
                response,
                msgtype="m.notice"
            )
            
            # Mettre à jour l'historique
            await update_conversation_history(
                config,
                room_id,
                sender,
                user_message=f"Explorer le lien {url}",
                bot_response=summary
            )
            
            return
        
        # Si pas en cache, extraire le contenu avec HTTPX et BS4
        result = await fetch_and_parse_url(url)
        
        if "error" in result:
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ Erreur lors de l'exploration du lien: {result['error']}",
                msgtype="m.notice"
            )
            return
        
        content = result["content"]
        
        # Initialiser le client API Albert uniquement pour la génération du résumé
        api_key = config.albert_api_token
        base_url = config.albert_api_url
        aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
        
        # Construire le prompt pour résumer
        summary_prompt = f"""
Tu es un assistant spécialisé dans l'analyse et le résumé de pages web.
Voici le contenu de la page {url}.
Fais-en un résumé structuré et informatif, en identifiant:
1. Le sujet principal
2. Les informations clés
3. La structure du site (si visible)
4. Les actions possibles ou recommandées

Contenu de la page:
{content}

Présente ta réponse de manière claire et organisée.
"""
        
        # Générer le résumé
        summary = await aclient.generate(
            model=config.albert_model,
            messages=[{"role": "user", "content": summary_prompt}]
        )
        
        # Mettre en cache le résultat
        cache_data = {
            "summary": summary,
            "url": url,
            "content": content,
            "created_at": time.time()
        }
        
        # Cache valide pour 1 jour (les sites peuvent changer)
        await web_search_cache.set(cache_key, cache_data, "explorer", 24 * 60 * 60)
        
        # Enregistrer la visite
        web_links_manager = await get_web_links_manager(config)
        await web_links_manager.increment_visit(url)
        
        # Format de la réponse
        response = f"**Résumé de [{url}]({url})**\n\n{summary}"
        
        # Proposer d'ajouter le lien à la base de données s'il n'y est pas déjà
        all_links = await web_links_manager.get_all_links()
        link_exists = any(link["url"] == url for link in all_links)
        
        if not link_exists:
            response += "\n\n---\n\nCe lien n'est pas dans la base de données. Pour l'ajouter :\n"
            response += f"`!ajouter_lien {url}`"
        
        # Envoyer la réponse
        await matrix_client.send_markdown_message(
            room_id,
            response,
            msgtype="m.notice"
        )
        
        # Enregistrer dans l'historique
        await update_conversation_history(
            config,
            room_id,
            sender,
            user_message=f"Explorer le lien {url}",
            bot_response=summary
        )
    
    except Exception as e:
        logger.error(f"Erreur lors de l'exploration du lien: {str(e)}")
        await matrix_client.send_markdown_message(
            room_id,
            f"⚠️ Erreur lors de l'exploration du lien: {str(e)}",
            msgtype="m.notice"
        )
```

## Avantages et inconvénients

### Avantages
1. **Indépendance**: Pas besoin de dépendre de l'API d'Albert pour la récupération du contenu web
2. **Performance**: Requêtes directes plus rapides 
3. **Contrôle**: Maîtrise complète du processus d'extraction et de nettoyage
4. **Flexibilité**: Possibilité d'implémenter des filtres, extracteurs spécifiques aux sites

### Inconvénients
1. **Complexité**: Nécessite de gérer les erreurs, timeouts, et formats de réponse variés
2. **Blocage**: Certains sites peuvent bloquer les robots ou requêtes automatisées
3. **Maintenance**: Les sites évoluent, nécessitant des mises à jour des parsers

## Installation des dépendances

Pour utiliser ces alternatives, vous auriez besoin d'installer Beautiful Soup 4 et potentiellement d'autres bibliothèques:

```bash
pip install beautifulsoup4 trafilatura newspaper3k
```

Puis ajoutez ces dépendances dans pyproject.toml:

```toml
dependencies = [
    # ... autres dépendances
    "beautifulsoup4==4.12.2",
    "trafilatura==1.6.0",
    "newspaper3k==0.2.8",
]
``` 