"""
Service de gestion du contenu web pour ColaIG.
Stocke, vectorise et recherche dans le contenu des pages web.
"""

import json
import os
import time
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import hashlib

from app.config import Config
from app.matrix_bot.config import logger
from app.services.web_links_manager import get_web_links_manager
from app.services.browser_extraction import extract_web_content
from app.core_llm import AlbertApiClient

class WebContentManager:
    """
    Gestionnaire de contenu web pour ColaIG.
    Étend le WebLinksManager pour stocker et rechercher dans le contenu des pages.
    """
    
    # Chemins pour le stockage
    CONTENT_DIR = ".albert/web_links/content/"
    VECTORS_DIR = ".albert/web_links/vectors/"
    
    # Seuil de fraîcheur (en jours) - Plus agressif pour garantir la fraîcheur
    FRESHNESS_THRESHOLD = 3  # 3 jours au lieu de 7
    
    # Seuils de fraîcheur adaptatifs selon le type de contenu
    FRESHNESS_THRESHOLDS = {
        "news": 1,      # Actualités : 1 jour
        "blog": 3,      # Blogs : 3 jours  
        "docs": 7,      # Documentation : 7 jours
        "general": 3    # Général : 3 jours
    }
    
    def __init__(self, config: Config, webdav_service=None, room_id=None, user_id=None):
        self.config = config
        self.webdav_service = webdav_service
        self.loaded = False
        # Stocker les informations de contexte
        self.room_id = room_id
        self.user_id = user_id
        
    async def ensure_initialized(self):
        """Assure que le gestionnaire est initialisé avec les répertoires nécessaires."""
        if not self.loaded:
            if not self.webdav_service:
                from app.services.webdav_instance import get_webdav_service
                # Utiliser le service WebDAV contextuel si des informations de contexte sont disponibles
                self.webdav_service = await get_webdav_service(
                    self.config,
                    room_id=self.room_id,
                    user_id=self.user_id
                )
            
            # Créer les répertoires s'ils n'existent pas
            for path in [self.CONTENT_DIR, self.VECTORS_DIR]:
                full_path = os.path.join(self.config.webdav_root_path, path)
                if not await self.webdav_service.exists(full_path):
                    await self.webdav_service.create_directory(full_path)
            
            self.loaded = True
    
    def _generate_url_hash(self, url: str) -> str:
        """Génère un hash unique pour une URL."""
        return hashlib.md5(url.encode()).hexdigest()
    
    def _detect_content_type(self, url: str, title: str = "", content: str = "") -> str:
        """Détecte le type de contenu pour adapter le seuil de fraîcheur."""
        url_lower = url.lower()
        title_lower = title.lower()
        content_lower = content.lower()
        
        # Détection des actualités
        if any(keyword in url_lower for keyword in ['news', 'actualit', 'info', 'press', 'breaking']):
            return "news"
        if any(keyword in title_lower for keyword in ['actualité', 'news', 'breaking', 'urgent']):
            return "news"
        
        # Détection des blogs
        if any(keyword in url_lower for keyword in ['blog', 'post', 'article']):
            return "blog"
        
        # Détection de la documentation
        if any(keyword in url_lower for keyword in ['doc', 'guide', 'manual', 'wiki', 'help']):
            return "docs"
        
        return "general"
    
    async def store_page_content(self, url: str, content: str, summary: str = None, title: str = None):
        """Stocke le contenu textuel d'une page web."""
        await self.ensure_initialized()
        
        url_hash = self._generate_url_hash(url)
        content_path = os.path.join(self.config.webdav_root_path, self.CONTENT_DIR, f"{url_hash}.json")
        
        # Créer la structure de données
        data = {
            "url": url,
            "title": title,
            "content": content,
            "summary": summary,
            "collected_at": datetime.now().isoformat(),
            "content_length": len(content)
        }
        
        # Stocker le contenu
        try:
            await self.webdav_service.write_file(
                content_path, 
                json.dumps(data, ensure_ascii=False, indent=2)
            )
            logger.info(f"Contenu stocké pour {url} ({len(content)} caractères)")
            return True
        except Exception as e:
            logger.error(f"Erreur lors du stockage du contenu pour {url}: {str(e)}")
            return False
    
    async def store_page_vectors(self, url: str, vectors: List[Dict[str, Any]]):
        """Stocke les vecteurs d'embedding pour une page web."""
        await self.ensure_initialized()
        
        url_hash = self._generate_url_hash(url)
        vectors_path = os.path.join(self.config.webdav_root_path, self.VECTORS_DIR, f"{url_hash}.json")
        
        # Créer la structure de données
        data = {
            "url": url,
            "vectors": vectors,
            "embedding_model": self.config.albert_model_embedding,
            "created_at": datetime.now().isoformat()
        }
        
        # Stocker les vecteurs
        try:
            await self.webdav_service.write_file(
                vectors_path, 
                json.dumps(data, ensure_ascii=False, indent=2)
            )
            logger.info(f"Vecteurs stockés pour {url} ({len(vectors)} chunks)")
            return True
        except Exception as e:
            logger.error(f"Erreur lors du stockage des vecteurs pour {url}: {str(e)}")
            return False
    
    async def get_page_content(self, url: str) -> Optional[Dict[str, Any]]:
        """Récupère le contenu d'une page web stockée."""
        await self.ensure_initialized()
        
        url_hash = self._generate_url_hash(url)
        content_path = os.path.join(self.config.webdav_root_path, self.CONTENT_DIR, f"{url_hash}.json")
        
        if await self.webdav_service.exists(content_path):
            try:
                content_str = await self.webdav_service.read_document(content_path)
                return json.loads(content_str)
            except Exception as e:
                logger.error(f"Erreur lors de la lecture du contenu pour {url}: {str(e)}")
        
        return None
    
    async def is_content_fresh(self, url: str, content_type: str = None) -> bool:
        """Vérifie si le contenu stocké est suffisamment récent selon son type."""
        content_data = await self.get_page_content(url)
        if not content_data:
            return False
        
        try:
            collected_at = datetime.fromisoformat(content_data.get("collected_at", "2000-01-01"))
            
            # Utiliser le type de contenu pour déterminer le seuil
            if content_type is None:
                content_type = self._detect_content_type(
                    url, 
                    content_data.get("title", ""), 
                    content_data.get("content", "")
                )
            
            threshold_days = self.FRESHNESS_THRESHOLDS.get(content_type, self.FRESHNESS_THRESHOLD)
            threshold = datetime.now() - timedelta(days=threshold_days)
            
            is_fresh = collected_at > threshold
            logger.debug(f"Fraîcheur {url}: {content_type}, {threshold_days}j, {'✅' if is_fresh else '❌'}")
            
            return is_fresh
        except Exception as e:
            logger.error(f"Erreur lors de la vérification de fraîcheur pour {url}: {str(e)}")
            return False
    
    async def get_content_age_info(self, url: str) -> Dict[str, Any]:
        """Retourne des informations détaillées sur l'âge du contenu."""
        content_data = await self.get_page_content(url)
        if not content_data:
            return {"exists": False}
        
        try:
            collected_at = datetime.fromisoformat(content_data.get("collected_at", "2000-01-01"))
            now = datetime.now()
            age_delta = now - collected_at
            
            content_type = self._detect_content_type(
                url, 
                content_data.get("title", ""), 
                content_data.get("content", "")
            )
            
            threshold_days = self.FRESHNESS_THRESHOLDS.get(content_type, self.FRESHNESS_THRESHOLD)
            is_fresh = age_delta.days <= threshold_days
            
            return {
                "exists": True,
                "collected_at": collected_at.isoformat(),
                "age_days": age_delta.days,
                "age_hours": int(age_delta.total_seconds() / 3600),
                "content_type": content_type,
                "threshold_days": threshold_days,
                "is_fresh": is_fresh,
                "freshness_ratio": min(1.0, threshold_days / max(1, age_delta.days))
            }
        except Exception as e:
            logger.error(f"Erreur lors du calcul de l'âge pour {url}: {str(e)}")
            return {"exists": True, "error": str(e)}
    
    async def search_stored_content(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Recherche dans le contenu stocké en utilisant les embeddings.
        Retourne les sites les plus pertinents (pas les chunks individuels).
        """
        await self.ensure_initialized()
        
        # 1. Obtenir l'embedding de la requête
        aclient = AlbertApiClient(
            base_url=self.config.albert_api_url, 
            api_key=self.config.albert_api_token
        )
        
        try:
            # Obtenir les embeddings de la requête
            try:
                query_embeddings = await aclient.get_embeddings(
                    texts=[query],
                    model=self.config.albert_model_embedding
                )
                query_embedding = query_embeddings[0] if query_embeddings else None
            except Exception as e:
                logger.error(f"Erreur lors de l'obtention de l'embedding de la requête: {str(e)}")
                return []
            
            if not query_embedding:
                logger.warning("Impossible d'obtenir l'embedding de la requête")
                return []
            
            # 2. Rechercher les documents similaires par chunks
            chunk_results = []
            
            # Obtenir la liste des fichiers d'embeddings
            vectors_dir = os.path.join(self.config.webdav_root_path, self.VECTORS_DIR)
            
            try:
                files = await self.webdav_service.list_directory(vectors_dir)
            except Exception as e:
                logger.warning(f"Impossible de lister le répertoire des vecteurs: {str(e)}")
                return []
            
            # Pour chaque fichier d'embeddings
            for file_info in files:
                # file_info est un dictionnaire avec 'name', 'path', 'type', etc.
                if file_info.get('type') != 'file' or not file_info.get('name', '').endswith('.json'):
                    continue
                
                try:
                    # Lire les vecteurs
                    file_path = os.path.join(vectors_dir, file_info['name'])
                    content = await self.webdav_service.read_document(file_path)
                    vector_data = json.loads(content)
                    
                    # Vérifier si c'est le même modèle d'embedding
                    if vector_data.get("embedding_model") != self.config.albert_model_embedding:
                        continue
                    
                    # Calculer la similarité pour chaque chunk
                    url = vector_data.get("url", "")
                    
                    for i, vec_item in enumerate(vector_data.get("vectors", [])):
                        vec = vec_item.get("vector", [])
                        
                        if vec and query_embedding:
                            try:
                                # Calculer la similarité cosinus
                                import numpy as np
                                similarity = np.dot(vec, query_embedding) / (np.linalg.norm(vec) * np.linalg.norm(query_embedding))
                                
                                chunk_results.append({
                                    "url": url,
                                    "chunk_id": i,
                                    "chunk_text": vec_item.get("text", ""),
                                    "similarity": float(similarity)
                                })
                            except Exception as e:
                                logger.debug(f"Erreur calcul similarité pour chunk {i} de {url}: {str(e)}")
                                continue
                
                except Exception as e:
                    logger.warning(f"Erreur lors du traitement du fichier {file_info.get('name', 'inconnu')}: {str(e)}")
                    continue
            
            # 3. Regrouper les résultats par site et calculer le score global
            sites_scores = {}
            sites_best_chunks = {}
            
            for chunk_result in chunk_results:
                url = chunk_result["url"]
                similarity = chunk_result["similarity"]
                
                if url not in sites_scores:
                    sites_scores[url] = []
                    sites_best_chunks[url] = chunk_result
                
                sites_scores[url].append(similarity)
                
                # Garder le chunk avec le meilleur score pour ce site
                if similarity > sites_best_chunks[url]["similarity"]:
                    sites_best_chunks[url] = chunk_result
            
            # 4. Calculer le score final pour chaque site
            site_results = []
            
            for url, similarities in sites_scores.items():
                # Score final = moyenne pondérée entre le meilleur score et la moyenne
                max_score = max(similarities)
                avg_score = sum(similarities) / len(similarities)
                final_score = (max_score * 0.7) + (avg_score * 0.3)  # 70% meilleur, 30% moyenne
                
                # Récupérer les métadonnées du contenu
                content_data = await self.get_page_content(url)
                
                site_result = {
                    "url": url,
                    "title": content_data.get("title", url.split("/")[-1]) if content_data else url.split("/")[-1],
                    "summary": content_data.get("summary", "") if content_data else "",
                    "similarity": final_score,
                    "chunks_matched": len(similarities),
                    "best_chunk_text": sites_best_chunks[url]["chunk_text"][:200] + "..." if len(sites_best_chunks[url]["chunk_text"]) > 200 else sites_best_chunks[url]["chunk_text"],
                    "content_length": content_data.get("content_length", 0) if content_data else 0,
                    "collected_at": content_data.get("collected_at", "") if content_data else ""
                }
                
                site_results.append(site_result)
            
            # 5. Trier par score final et prendre les top_k sites
            site_results.sort(key=lambda x: x["similarity"], reverse=True)
            return site_results[:top_k]
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche dans le contenu stocké: {str(e)}")
            return []
        finally:
            await aclient.close()
    
    def _chunk_content(self, content: str, chunk_size: int = 1000) -> List[str]:
        """Divise le contenu en chunks de taille appropriée."""
        chunks = []
        
        # Division simple en chunks de taille fixe avec chevauchement
        words = content.split()
        current_chunk = []
        current_size = 0
        overlap_size = 100  # Mots de chevauchement
        
        for word in words:
            current_chunk.append(word)
            current_size += len(word) + 1  # +1 pour l'espace
            
            if current_size >= chunk_size:
                chunks.append(" ".join(current_chunk))
                
                # Créer le chevauchement pour le chunk suivant
                if len(current_chunk) > overlap_size:
                    current_chunk = current_chunk[-overlap_size:]
                    current_size = sum(len(word) + 1 for word in current_chunk)
                else:
                    current_chunk = []
                    current_size = 0
        
        # Ajouter le dernier chunk s'il existe
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        
        return chunks
    
    async def process_and_store_page(self, url: str) -> Dict[str, Any]:
        """
        Extrait, résume, vectorise et stocke le contenu d'une page web.
        Retourne les métadonnées de traitement.
        """
        await self.ensure_initialized()
        
        try:
            logger.info(f"Début du traitement de la page: {url}")
            
            # 1. Extraire le contenu
            extraction_result = await extract_web_content(url, self.config)
            content = extraction_result.get("content", "")
            title = extraction_result.get("title", url.split("/")[-1])
            
            if not content:
                raise ValueError(f"Impossible d'extraire le contenu de {url}")
            
            logger.info(f"Contenu extrait: {len(content)} caractères")
            
            # 2. Générer un résumé
            aclient = AlbertApiClient(
                base_url=self.config.albert_api_url, 
                api_key=self.config.albert_api_token
            )
            
            # Tronquer le contenu pour le résumé si nécessaire
            content_for_summary = content[:8000] if len(content) > 8000 else content
            
            summary_prompt = f"""Tu es un assistant qui résume efficacement le contenu d'une page web.
            
Voici le contenu extrait de {url} :

---
{content_for_summary}
---

Fais-en un résumé clair et concis qui capture les informations essentielles.
Organise le résumé en sections si c'est pertinent.
Ton résumé ne doit pas dépasser 250 mots. Ne mentionne pas que tu es un modèle d'IA."""
            
            summary = await aclient.generate(
                model=self.config.albert_model,
                messages=[{"role": "user", "content": summary_prompt}]
            )
            
            logger.info(f"Résumé généré: {len(summary)} caractères")
            
            # 3. Chunker le contenu pour l'embedding
            chunks = self._chunk_content(content, chunk_size=1000)
            logger.info(f"Contenu divisé en {len(chunks)} chunks")
            
            # 4. Générer les embeddings pour chaque chunk
            vectors = []
            
            # Traiter les chunks par lots pour éviter les limitations d'API
            batch_size = min(5, len(chunks))  # Limiter à 5 chunks par requête
            
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i:i+batch_size]
                
                try:
                    # Obtenir les embeddings pour ce lot
                    embeddings = await aclient.get_embeddings(
                        texts=batch,
                        model=self.config.albert_model_embedding
                    )
                    
                    # Ajouter à la liste des vecteurs
                    for j, embedding in enumerate(embeddings):
                        vectors.append({
                            "text": batch[j],
                            "vector": embedding,
                            "position": i + j
                        })
                    
                    # Petit délai pour éviter de surcharger l'API
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Erreur lors de l'embedding du lot {i//batch_size + 1}: {str(e)}")
                    # Continuer avec les autres lots même si un échoue
                    continue
            
            logger.info(f"Embeddings générés pour {len(vectors)} chunks")
            
            # 5. Stocker le contenu et les vecteurs
            content_stored = await self.store_page_content(url, content, summary, title)
            vectors_stored = await self.store_page_vectors(url, vectors) if vectors else False
            
            # 6. Mettre à jour le lien dans le gestionnaire de liens
            web_links_manager = await get_web_links_manager(self.config)
            
            # Trouver la catégorie existante si le lien existe déjà
            all_links = await web_links_manager.get_all_links()
            existing_category = None
            
            for link in all_links:
                if link["url"] == url:
                    existing_category = link.get("category", "general")
                    break
            
            # Ajouter ou mettre à jour le lien
            await web_links_manager.add_link(
                url=url,
                title=title,
                category=existing_category or "general",
                description=summary[:100] + "..." if len(summary) > 100 else summary
            )
            
            await aclient.close()
            
            return {
                "url": url,
                "title": title,
                "summary": summary,
                "chunks_count": len(chunks),
                "vectors_count": len(vectors),
                "content_length": len(content),
                "content_stored": content_stored,
                "vectors_stored": vectors_stored,
                "status": "success"
            }
            
        except Exception as e:
            logger.error(f"Erreur lors du traitement de la page {url}: {str(e)}")
            return {
                "url": url,
                "status": "error",
                "error": str(e)
            }

# Instance unique
_web_content_manager = None

async def get_web_content_manager(config: Config, room_id=None, user_id=None) -> WebContentManager:
    """
    Récupère une instance du gestionnaire de contenu web.
    
    Args:
        config: Configuration générale
        room_id: ID du salon Matrix (optionnel)
        user_id: ID de l'utilisateur (optionnel)
        
    Returns:
        Une instance du gestionnaire de contenu web adaptée au contexte
    """
    # Utiliser le service WebDAV contextuel si possible
    webdav_service = None
    if room_id or user_id:
        from app.services.webdav_instance import get_webdav_service
        try:
            webdav_service = await get_webdav_service(
                config,
                room_id=room_id,
                user_id=user_id
            )
            logger.debug(f"Service WebDAV contextuel utilisé pour room_id={room_id}, user_id={user_id}")
        except Exception as e:
            logger.error(f"Erreur lors de l'obtention du service WebDAV contextuel: {str(e)}")
            # Continuer sans webdav_service, il sera créé dans ensure_initialized
    
    # Créer l'instance avec les informations de contexte
    return WebContentManager(
        config=config,
        webdav_service=webdav_service,
        room_id=room_id,
        user_id=user_id
    ) 