from typing import List, Dict, Any, Optional, Set, Tuple, Union
from dataclasses import dataclass
import asyncio
import time

from app.matrix_bot.config import logger
from app.config import Config
from app.core_llm import AlbertApiClient
from app.services.index_service import IndexService
from app.services.webdav import WebDAVService
from app.services.context.models import SessionContext, RoomContext

SYNTHESIS_SYSTEM_PROMPT = '''
Tu es Colaig, un assistant automatique de l'État français spécialisé dans la synthèse d'information pour les agents publics.

OBJECTIF DE SYNTHÈSE :
- Créer une synthèse complète et structurée sur le sujet demandé
- Organiser l'information par thèmes et sous-thèmes pertinents
- Présenter une vue d'ensemble exhaustive et cohérente
- Identifier les concepts clés et leurs relations

PRINCIPES DE STRUCTURATION :
- Hiérarchiser l'information de manière logique et progressive
- Distinguer clairement les différentes sections thématiques
- Mettre en évidence les points essentiels et consensus
- Présenter les nuances et divergences éventuelles

FORMAT DE RÉPONSE :
- Introduction contextualisant le sujet
- Corps structuré en sections thématiques
- Conclusion synthétique
- Citations précises des sources avec références

Tu dois être factuel, précis et exhaustif dans ta synthèse. Utilise toutes les informations fournies pour créer un panorama complet du sujet.
'''

class SynthesisRagAction:
    """Action RAG optimisée pour la synthèse globale d'un sujet avec reranking"""

    def __init__(self, config: Config):
        self.config = config
        self.index_service = None
        self.webdav_service = None
        self.albert_client = None
        self._initialized = False
        self.max_chunks_initial = 50  # Nombre de chunks initial à récupérer
        self.max_chunks_final = 25   # Nombre de chunks après reranking
        
    async def initialize(self) -> None:
        """Initialise le service avec les dépendances nécessaires"""
        if self._initialized:
            return
            
        try:
            # Initialiser WebDAV si nécessaire
            if not self.webdav_service:
                self.webdav_service = WebDAVService(self.config)
                await self.webdav_service.initialize()
            elif not self.webdav_service._initialized:
                await self.webdav_service.initialize()
                
            # Initialiser IndexService si nécessaire
            if not self.index_service:
                self.index_service = IndexService(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
                await self.index_service.initialize(
                    init_document_index=True,
                    init_behavior_manager=True
                )
                
            # Initialiser AlbertClient
            self.albert_client = AlbertApiClient(
                base_url=self.config.albert_api_url,
                api_key=self.config.albert_api_token
            )
                
            self._initialized = True
            logger.info("SynthesisRagAction initialisé avec succès")
                
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation de SynthesisRagAction: {str(e)}")
            # Nettoyer les ressources en cas d'échec
            if self.webdav_service:
                await self.webdav_service.close()
            if self.albert_client:
                await self.albert_client.close()
            self._initialized = False
            raise

    async def close(self) -> None:
        """Ferme proprement les ressources"""
        if self.albert_client:
            await self.albert_client.close()
        if self.webdav_service:
            await self.webdav_service.close()
        self._initialized = False

    async def retrieve_comprehensive_chunks(
        self,
        query: str,
        session_context: Optional[SessionContext] = None,
        room_context: Optional[RoomContext] = None
    ) -> List[Dict[str, Any]]:
        """
        Récupère un ensemble complet de chunks pertinents pour le sujet
        
        Args:
            query: La requête ou le sujet pour la synthèse
            session_context: Contexte de session optionnel
            room_context: Contexte de salon optionnel
            
        Returns:
            Liste étendue de chunks pertinents
        """
        logger.info(f"Récupération de chunks pour synthèse sur: {query}")
        
        if not self._initialized:
            await self.initialize()
            
        # Extraire le sujet principal de la requête
        enhanced_query = self._enhance_query_for_synthesis(query, session_context)
        
        try:
            # Utiliser l'index FAISS local via IndexService au lieu des collections Albert API
            if self.index_service and self.index_service.document_index:
                chunks = await self.index_service.search(
                    query=enhanced_query,
                    limit=self.max_chunks_initial,
                    index_type="document",
                    search_mode="precise"
                )
                
                if not chunks:
                    logger.warning(f"Aucun chunk trouvé pour la requête: {query}")
                    return []
                    
                logger.info(f"Récupération réussie: {len(chunks)} chunks pour la synthèse")
                return chunks
            else:
                logger.warning("Service d'index ou index de documents non disponible")
                return []
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des chunks: {str(e)}")
            return []

    async def process_for_synthesis(
        self,
        query: str,
        chunks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Applique le reranking et organise les chunks pour la synthèse
        
        Args:
            query: La requête ou sujet de la synthèse
            chunks: Liste initiale de chunks à traiter
            
        Returns:
            Liste organisée et réordonnée de chunks
        """
        if not chunks:
            return []
            
        try:
            # Format attendu par l'API Albert pour le reranking:
            # Liste de textes simples sans structure complexe
            query_texts = []
            docs_map = {}
            
            for i, chunk in enumerate(chunks):
                # Extraire et simplifier le contenu textuel pour l'API
                content = chunk.get("content", "")
                
                # Simplifier le texte pour éviter les problèmes avec l'API
                # - Retirer les métadonnées du document et les informations structurées 
                # - Ne garder que le contenu textuel pur
                # Trouver la ligne qui commence par "Contenu:" si elle existe
                if "Contenu:" in content:
                    # Extraire seulement le contenu après "Contenu:"
                    simple_text = content.split("Contenu:", 1)[1].strip()
                else:
                    # Sinon utiliser tout le texte mais limité à 1000 caractères
                    simple_text = content[:1000]
                
                # Si le texte est vide ou trop court après simplification, utiliser l'original
                if len(simple_text) < 10:
                    simple_text = content[:1000]
                
                query_texts.append(simple_text)
                # Mapper l'index au chunk original pour reconstruction ultérieure
                docs_map[i] = chunk
                
            logger.info(f"Début du reranking avec {len(query_texts)} chunks")
            
            try:
                # Vérifier qu'on a des textes non vides
                if not query_texts or all(not text.strip() for text in query_texts):
                    raise ValueError("Tous les textes sont vides après traitement")
                
                # Construire le payload selon la documentation de l'API
                payload = {
                    "prompt": query,  # Paramètre "prompt" au lieu de "query"
                    "input": query_texts,  # Paramètre "input" au lieu de "documents"
                    "model": "rerank-small"  # Nom du modèle correct
                }
                
                if len(query_texts) > 0:
                    logger.info(f"Premier document simplifié (exemple): '{query_texts[0][:100]}...'")
                
                # Appel direct à l'API avec httpx pour plus de contrôle
                response = await self.albert_client.http_client.post(
                    f"{self.albert_client.base_url}/v1/rerank",
                    json=payload
                )
                response.raise_for_status()
                rerank_results = response.json()
                
                # Log pour debug détaillé
                logger.info(f"Réponse brute du reranking: {rerank_results}")
                
                # Format attendu de la réponse selon la doc:
                # {"object": "list", "data": [{"score": 0.98, "index": 2}, ...]}
                
                # Vérifier la structure de base de la réponse
                if not rerank_results:
                    logger.warning("Réponse vide du reranking")
                    raise ValueError("Réponse vide reçue de l'API de reranking")
                
                # Vérifier et extraire les données selon le format attendu
                if isinstance(rerank_results, dict):
                    if "object" in rerank_results and rerank_results["object"] == "list":
                        if "data" in rerank_results and isinstance(rerank_results["data"], list):
                            results = rerank_results["data"]
                            logger.info(f"Format standard de l'API Albert détecté, {len(results)} résultats")
                        else:
                            logger.warning("Format de réponse invalide: clé 'data' manquante ou non valide")
                            raise ValueError("Format de réponse invalide: clé 'data' manquante ou non valide")
                    elif "results" in rerank_results:
                        # Format ancien/alternatif
                        results = rerank_results.get("results", [])
                        logger.info(f"Format alternatif détecté, {len(results)} résultats")
                    else:
                        # Autre format de dictionnaire, essayer d'utiliser tel quel
                        results = rerank_results
                        logger.warning(f"Format de réponse non standard: {list(rerank_results.keys())}")
                elif isinstance(rerank_results, list):
                    # Liste directe de résultats
                    results = rerank_results
                    logger.info(f"Format de liste directe détecté, {len(results)} résultats")
                else:
                    logger.warning(f"Format de réponse totalement inattendu: {type(rerank_results)}")
                    raise ValueError(f"Format de réponse inattendu: {type(rerank_results)}")
                
                if not results:
                    logger.warning("Aucun résultat exploitable dans la réponse")
                    raise ValueError("Aucun résultat exploitable dans la réponse")
                
                # Log d'exemple pour debug
                if results:
                    logger.info(f"Exemple de résultat: {results[0]}")
                    
            except Exception as rerank_error:
                logger.error(f"Erreur lors du reranking: {str(rerank_error)}")
                # En cas d'erreur, utiliser le tri par similarité
                return sorted(
                    chunks, 
                    key=lambda x: x.get("metadata", {}).get("similarity_score", 0),
                    reverse=True
                )[:self.max_chunks_final]
            
            # Traitement des résultats du reranking
            processed_chunks = []
            
            for result in results:
                try:
                    # Extraction des informations du résultat
                    if isinstance(result, dict):
                        # L'index nous permet de récupérer le chunk original
                        idx = result.get("index")
                        if idx is not None and idx < len(chunks):
                            # Récupérer le chunk original
                            original_chunk = docs_map.get(idx, {})
                            if not original_chunk:
                                continue
                            
                            # Créer une copie pour éviter de modifier l'original
                            processed_chunk = original_chunk.copy()
                            
                            # Ajouter le score au metadata
                            if "metadata" not in processed_chunk:
                                processed_chunk["metadata"] = {}
                            processed_chunk["metadata"]["rerank_score"] = result.get("score", 0.0)
                            
                            processed_chunks.append(processed_chunk)
                    
                except Exception as chunk_error:
                    logger.error(f"Erreur lors du traitement d'un chunk: {str(chunk_error)}")
                    continue
            
            # Si aucun chunk n'a été traité correctement, utiliser une solution de secours
            if not processed_chunks:
                logger.warning("Aucun chunk n'a pu être traité après reranking, utilisation des chunks originaux")
                return sorted(
                    chunks, 
                    key=lambda x: x.get("metadata", {}).get("similarity_score", 0),
                    reverse=True
                )[:self.max_chunks_final]
            
            # Regrouper les chunks par document et section
            organized_chunks = self._organize_chunks_by_source(processed_chunks)
            
            return organized_chunks
            
        except Exception as e:
            logger.error(f"Erreur lors du traitement pour synthèse: {str(e)}")
            # En cas d'erreur, effectuer un tri local basique
            logger.info("Utilisation du tri local comme solution de secours")
            sorted_chunks = sorted(
                chunks, 
                key=lambda x: x.get("metadata", {}).get("similarity_score", 0),
                reverse=True
            )
            return sorted_chunks[:self.max_chunks_final]

    def _organize_chunks_by_source(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Organise les chunks par source et section pour une meilleure cohérence
        
        Args:
            chunks: Liste de chunks à organiser
            
        Returns:
            Liste réorganisée de chunks
        """
        # Regrouper par document
        docs_map = {}
        for chunk in chunks:
            doc_name = chunk.get("metadata", {}).get("document_name", "inconnu")
            if doc_name not in docs_map:
                docs_map[doc_name] = []
            docs_map[doc_name].append(chunk)
        
        # Trier les chunks de chaque document par position/page
        for doc_name, doc_chunks in docs_map.items():
            doc_chunks.sort(key=lambda x: (
                x.get("metadata", {}).get("page", 0),
                x.get("metadata", {}).get("chunk_id", 0)
            ))
        
        # Réassembler en alternant entre les documents pour éviter les répétitions
        organized_chunks = []
        while any(docs_map.values()):
            for doc_name in list(docs_map.keys()):
                if docs_map[doc_name]:
                    organized_chunks.append(docs_map[doc_name].pop(0))
                else:
                    docs_map.pop(doc_name)
                    
        return organized_chunks

    def _enhance_query_for_synthesis(
        self,
        query: str,
        session_context: Optional[SessionContext] = None
    ) -> str:
        """
        Améliore la requête pour une recherche plus exhaustive
        
        Args:
            query: Requête originale
            session_context: Contexte de session
            
        Returns:
            Requête améliorée
        """
        # Identifier le sujet principal
        if "synthèse" in query.lower() or "résumé" in query.lower():
            # Extraire le sujet après les mots clés
            for keyword in ["synthèse sur", "synthèse de", "résumé sur", "résumé de"]:
                if keyword in query.lower():
                    subject = query.lower().split(keyword, 1)[1].strip()
                    if subject:
                        return f"information complète et exhaustive sur {subject}"
        
        # Si pas de mot-clé spécifique, utiliser la requête complète
        return f"information complète et détaillée sur {query}"

    async def generate_synthesis(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        session_context: Optional[SessionContext] = None
    ) -> str:
        """
        Génère une synthèse structurée à partir des chunks
        
        Args:
            query: La requête ou sujet de la synthèse
            chunks: Liste de chunks traités et organisés
            session_context: Contexte de session optionnel
            
        Returns:
            Synthèse complète et structurée
        """
        if not chunks:
            return "Je n'ai pas trouvé suffisamment d'informations sur ce sujet pour élaborer une synthèse."
            
        try:
            # Préparation du prompt pour la synthèse
            prompt = self._format_synthesis_prompt(query, chunks)
            
            # Appel à l'API pour générer la synthèse
            synthesis = await self.albert_client.generate(
                model=self.config.albert_model,
                messages=[
                    {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Température réduite pour favoriser la cohérence
                max_tokens=2000    # Limite assez large pour une synthèse complète
            )
            
            return synthesis
            
        except Exception as e:
            logger.error(f"Erreur lors de la génération de la synthèse: {str(e)}")
            return "Une erreur est survenue lors de la génération de la synthèse. Veuillez réessayer ultérieurement."

    def _format_synthesis_prompt(self, query: str, chunks: List[Dict[str, Any]]) -> str:
        """
        Formate le prompt pour la génération de la synthèse
        
        Args:
            query: Requête ou sujet
            chunks: Liste de chunks organisés
            
        Returns:
            Prompt structuré pour la synthèse
        """
        # Extraire le sujet principal
        subject = query
        for prefix in ["synthèse sur", "synthèse de", "résumé sur", "résumé de"]:
            if prefix in query.lower():
                subject = query.lower().split(prefix, 1)[1].strip()
                break
        
        # En-tête du prompt
        prompt = f"""Réalise une synthèse complète et structurée sur le sujet suivant : {subject}

CONTEXTE DOCUMENTAIRE :
Voici les informations pertinentes extraites de la base documentaire :

"""
        
        # Ajouter les chunks de manière organisée
        for i, chunk in enumerate(chunks):
            metadata = chunk.get("metadata", {})
            doc_name = metadata.get("document_name", "Document inconnu")
            section = metadata.get("section_title", "")
            page = metadata.get("page", "")
            score = metadata.get("rerank_score", metadata.get("similarity_score", ""))
            
            prompt += f"--- EXTRAIT {i+1} ---\n"
            prompt += f"Source: {doc_name}\n"
            if section:
                prompt += f"Section: {section}\n"
            if page:
                prompt += f"Page: {page}\n"
            prompt += f"\n{chunk['content']}\n\n"
        
        # Instructions de synthèse
        prompt += """
INSTRUCTIONS POUR LA SYNTHÈSE :
1. Structure ta réponse en sections thématiques clairement définies
2. Commence par une introduction qui présente le sujet et son importance
3. Organise le contenu de manière logique et progressive
4. Termine par une conclusion qui résume les points essentiels
5. Cite précisément les sources quand tu présentes des informations spécifiques

Ta synthèse doit être exhaustive et couvrir tous les aspects du sujet présents dans les documents fournis.
"""

        return prompt

    async def process_synthesis_request(
        self,
        query: str,
        session_context: Optional[SessionContext] = None,
        room_context: Optional[RoomContext] = None
    ) -> Union[str, Dict[str, Any]]:
        """
        Traite une demande de synthèse complète
        
        Args:
            query: La requête utilisateur
            session_context: Contexte de session
            room_context: Contexte de salon
            
        Returns:
            Dictionnaire contenant la synthèse générée et les sources utilisées
        """
        try:
            # 1. Récupérer un large ensemble de chunks
            chunks = await self.retrieve_comprehensive_chunks(
                query=query,
                session_context=session_context,
                room_context=room_context
            )
            
            if not chunks:
                return {
                    "synthesis": "Je n'ai pas trouvé d'informations sur ce sujet dans ma base documentaire.",
                    "sources": []
                }
                
            # 2. Appliquer le reranking et organiser les chunks
            processed_chunks = await self.process_for_synthesis(query, chunks)
            
            # 3. Générer la synthèse
            synthesis = await self.generate_synthesis(
                query=query,
                chunks=processed_chunks,
                session_context=session_context
            )
            
            # 4. Extraire les sources uniques
            unique_sources = {}
            for chunk in processed_chunks:
                metadata = chunk.get("metadata", {})
                doc_name = metadata.get("document_name", "Document inconnu")
                doc_path = metadata.get("document_path", "")
                
                if doc_name not in unique_sources:
                    # Créer une entrée pour ce document
                    unique_sources[doc_name] = {
                        "name": doc_name,
                        "path": doc_path,
                        "sections": set(),
                        "pages": set()
                    }
                
                # Ajouter les métadonnées disponibles
                if "section_title" in metadata and metadata["section_title"]:
                    unique_sources[doc_name]["sections"].add(metadata["section_title"])
                
                if "page" in metadata and metadata["page"]:
                    unique_sources[doc_name]["pages"].add(str(metadata["page"]))
            
            # Convertir les sets en listes pour la sortie JSON
            sources_list = []
            for doc_name in unique_sources:
                source = unique_sources[doc_name]
                sources_list.append({
                    "name": source["name"],
                    "path": source["path"],
                    "sections": list(source["sections"]),
                    "pages": list(source["pages"])
                })
            
            return {
                "synthesis": synthesis,
                "sources": sources_list
            }
            
        except Exception as e:
            logger.error(f"Erreur lors du traitement de la demande de synthèse: {str(e)}")
            return {
                "synthesis": "Une erreur est survenue lors de la génération de la synthèse. Veuillez réessayer ultérieurement.",
                "sources": []
            }
            
    async def __aenter__(self):
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close() 