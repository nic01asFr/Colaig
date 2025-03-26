from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import time

from matrix_bot.config import logger
from config import Config
from core_llm import AlbertApiClient
from services.index_service import IndexService
from services.webdav import WebDAVService
from services.context.models import SessionContext, RoomContext

COMMON_WORDS = {
    "le", "la", "les", "un", "une", "des", "ce", "cette", "ces",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
    "notre", "nos", "votre", "vos", "leur", "leurs",
    "et", "ou", "mais", "donc", "car", "ni", "or",
    "je", "tu", "il", "elle", "nous", "vous", "ils", "elles",
    "qui", "que", "quoi", "dont", "où",
    "dans", "sur", "sous", "avec", "sans", "pour", "par",
    "est", "sont", "être", "avoir", "faire",
    "plus", "moins", "très", "trop", "peu", "beaucoup"
}

@dataclass
class ConversationHistoryIndex:
    """Gère l'indexation et l'analyse de l'historique des conversations"""
    
    max_history_length: int = 10
    topic_memory_duration: int = 3600  # 1 heure
    
    def __init__(self):
        self.conversation_topics = {}  # room_id -> {topic: last_seen_timestamp}
        self.active_contexts = {}      # room_id -> List[str]
        self.topic_transitions = {}    # topic -> {next_topic: count}
    
    def update_history(
        self,
        room_id: str,
        message: Dict[str, Any],
        session_context: Optional[SessionContext]
    ) -> None:
        """Met à jour l'historique de conversation pour une salle"""
        current_time = time.time()
        
        # Extraire les topics du message
        message_topics = self._extract_topics([message["content"]])
        
        # Mettre à jour les topics actifs
        if room_id not in self.conversation_topics:
            self.conversation_topics[room_id] = {}
            
        # Nettoyer les vieux topics
        self._cleanup_old_topics(room_id, current_time)
        
        # Mettre à jour les timestamps des topics
        for topic in message_topics:
            self.conversation_topics[room_id][topic] = current_time
            
        # Mettre à jour les transitions de topics
        if session_context and session_context.history:
            previous_message = session_context.history[-1] if session_context.history else None
            if previous_message:
                prev_topics = self._extract_topics([previous_message["content"]])
                for prev_topic in prev_topics:
                    for curr_topic in message_topics:
                        if prev_topic not in self.topic_transitions:
                            self.topic_transitions[prev_topic] = {}
                        if curr_topic not in self.topic_transitions[prev_topic]:
                            self.topic_transitions[prev_topic][curr_topic] = 0
                        self.topic_transitions[prev_topic][curr_topic] += 1
    
    def get_active_topics(self, room_id: str) -> Set[str]:
        """Récupère les topics actifs pour une salle"""
        current_time = time.time()
        if room_id not in self.conversation_topics:
            return set()
            
        return {
            topic
            for topic, timestamp in self.conversation_topics[room_id].items()
            if current_time - timestamp < self.topic_memory_duration
        }
    
    def get_likely_next_topics(self, current_topics: Set[str]) -> Set[str]:
        """Prédit les topics probables suivants basés sur l'historique"""
        likely_topics = set()
        for topic in current_topics:
            if topic in self.topic_transitions:
                # Prendre les 3 transitions les plus fréquentes
                sorted_transitions = sorted(
                    self.topic_transitions[topic].items(),
                    key=lambda x: x[1],
                    reverse=True
                )
                likely_topics.update(topic for topic, _ in sorted_transitions[:3])
        return likely_topics
    
    def _cleanup_old_topics(self, room_id: str, current_time: float) -> None:
        """Nettoie les topics expirés"""
        if room_id in self.conversation_topics:
            self.conversation_topics[room_id] = {
                topic: timestamp
                for topic, timestamp in self.conversation_topics[room_id].items()
                if current_time - timestamp < self.topic_memory_duration
            }

class StandardMessageRagAction:
    """Action RAG optimisée pour les messages standards"""

    def __init__(self, config: Config):
        self.config = config
        self.index_service = None
        self.webdav_service = None
        self.conversation_history = ConversationHistoryIndex()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialise le service d'index en mode lecture seule"""
        if self._initialized:
            return
            
        try:
            # Réutiliser le service WebDAV existant si possible
            if not self.webdav_service:
                self.webdav_service = WebDAVService(self.config)
                await self.webdav_service.initialize()
            elif not self.webdav_service._initialized:
                await self.webdav_service.initialize()
                
            if not self.index_service:
                self.index_service = IndexService(
                    config=self.config,
                    webdav_service=self.webdav_service
                )
                try:
                    # Initialisation sans index documentaire
                    await self.index_service.initialize(
                        init_document_index=False, 
                        init_behavior_manager=True
                    )
                except Exception as e:
                    logger.error(f"Erreur initialisation index: {str(e)}")
                    # Nettoyer les ressources en cas d'échec
                    if self.webdav_service:
                        await self.webdav_service.close()
                    self._initialized = False
                    raise
                
            self._initialized = True
                
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du RAG: {str(e)}")
            # Nettoyer les ressources en cas d'échec
            if self.webdav_service:
                await self.webdav_service.close()
            self._initialized = False
            raise

    async def retrieve_relevant_chunks(
        self,
        query: str,
        session_context: Optional[SessionContext] = None,
        room_context: Optional[RoomContext] = None,
        search_mode: str = "hybrid",
    ) -> List[Dict[str, Any]]:
        """
        Récupère les chunks pertinents pour la requête utilisateur.
        
        Args:
            query: La requête de l'utilisateur.
            session_context: Le contexte de session de l'utilisateur (optionnel).
            room_context: Le contexte du salon (optionnel).
            search_mode: Mode de recherche à utiliser ('hybrid', 'semantic', 'fast')
        
        Returns:
            Une liste de DocumentChunk pertinents pour la requête.
        """
        logger.info(f"Récupération de chunks pertinents pour la requête: {query}")
        
        # Valider les paramètres
        if not query or not query.strip():
            logger.warning("Requête vide, aucun chunk ne sera récupéré")
            return []
            
        # En cas de réponse vide initiale, prévoir un conteneur
        relevant_chunks = []
        
        try:
            # Si l'index de documents n'est pas initialisé, essayer de l'initialiser
            if not self.index_service:
                try:
                    logger.info("Initialisation tardive de l'index de documents")
                    await self.index_service.initialize(
                        init_document_index=False, 
                        init_behavior_manager=True
                    )
                except Exception as init_err:
                    logger.error(f"Échec de l'initialisation tardive de l'index: {str(init_err)}")
                    return []
            
            # Effectuer la recherche selon le mode choisi
            if search_mode == "hybrid":
                # Recherche hybride : sémantique + mots-clés
                try:
                    # Recherche sémantique d'abord
                    semantic_chunks = await self.index_service.semantic_search(query, top_k=self.config.rag_top_k)
                    
                    # Si la recherche sémantique échoue, essayer la recherche par mots-clés
                    if not semantic_chunks:
                        logger.info("Recherche sémantique sans résultat, passage à la recherche par mots-clés")
                        relevant_chunks = await self.index_service.keyword_search(query, top_k=self.config.rag_top_k)
                    else:
                        relevant_chunks = semantic_chunks
                except Exception as hybrid_err:
                    logger.warning(f"Erreur lors de la recherche hybride: {str(hybrid_err)}")
                    # Fallback sur la recherche par mots-clés en cas d'erreur
                    try:
                        relevant_chunks = await self.index_service.keyword_search(query, top_k=self.config.rag_top_k)
                    except Exception as fallback_err:
                        logger.error(f"Échec du fallback sur la recherche par mots-clés: {str(fallback_err)}")
            
            elif search_mode == "semantic":
                # Recherche sémantique uniquement
                try:
                    relevant_chunks = await self.index_service.semantic_search(query, top_k=self.config.rag_top_k)
                except Exception as semantic_err:
                    logger.error(f"Échec de la recherche sémantique: {str(semantic_err)}")
            
            elif search_mode == "fast":
                # Recherche rapide par mots-clés uniquement
                try:
                    relevant_chunks = await self.index_service.keyword_search(query, top_k=self.config.rag_top_k)
                except Exception as keyword_err:
                    logger.error(f"Échec de la recherche par mots-clés: {str(keyword_err)}")
            
            else:
                # Mode inconnu, utiliser le mode hybride par défaut
                logger.warning(f"Mode de recherche inconnu '{search_mode}', utilisation du mode hybride par défaut")
                try:
                    relevant_chunks = await self.index_service.semantic_search(query, top_k=self.config.rag_top_k)
                    if not relevant_chunks:
                        relevant_chunks = await self.index_service.keyword_search(query, top_k=self.config.rag_top_k)
                except Exception as default_err:
                    logger.error(f"Échec de la recherche en mode par défaut: {str(default_err)}")
            
            # Journaliser les résultats
            if relevant_chunks:
                logger.info(f"Récupération de {len(relevant_chunks)} chunks pertinents")
                # Limiter la taille totale si nécessaire
                if len(relevant_chunks) > self.config.rag_max_chunks:
                    logger.info(f"Limitation à {self.config.rag_max_chunks} chunks (sur {len(relevant_chunks)})")
                    relevant_chunks = relevant_chunks[:self.config.rag_max_chunks]
            else:
                logger.warning("Aucun chunk pertinent trouvé pour cette requête")
                
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des chunks: {str(e)}")
            # Ne pas propager l'exception, retourner une liste vide
        
        return relevant_chunks

    def _filter_diverse_chunks(self, chunks: List[Dict]) -> List[Dict]:
        """Filtre les chunks pour assurer la diversité et éviter la redondance"""
        if not chunks:
            return []
            
        filtered_chunks = []
        seen_topics = set()
        
        for chunk in chunks:
            # Extraire les sujets du chunk
            chunk_content = chunk.get("content", "")
            chunk_topics = self._extract_topics([chunk_content])
            
            # Calculer le chevauchement avec les sujets déjà vus
            overlap_ratio = len(chunk_topics & seen_topics) / len(chunk_topics) if chunk_topics else 1.0
            
            # Ajouter le chunk s'il apporte suffisamment de nouveauté
            if overlap_ratio < 0.7:  # Seuil de diversité
                filtered_chunks.append(chunk)
                seen_topics.update(chunk_topics)
                
            # Limiter le nombre total de chunks
            if len(filtered_chunks) >= 5:
                break
                
        return filtered_chunks

    def _enhance_query(
        self,
        query: str,
        session_context: Optional[SessionContext],
        room_context: Optional[RoomContext]
    ) -> str:
        """Enrichit la requête avec le contexte conversationnel de manière optimisée"""
        query_parts = []

        try:
            # Extraire les sujets clés de la requête actuelle
            query_topics = self._extract_topics([query])
            
            if session_context and session_context.history:
                # Analyser l'historique récent pour la continuité thématique
                recent_messages = session_context.history[-3:]
                conversation_flow = []
                
                # Identifier les thèmes récurrents et les questions liées
                recurring_topics = set()
                related_questions = []
                
                for msg in recent_messages:
                    if msg["role"] == "user":
                        msg_topics = self._extract_topics([msg["content"]])
                        # Détecter les thèmes qui se répètent
                        if msg_topics & query_topics:
                            recurring_topics.update(msg_topics)
                        # Collecter les questions liées
                        if "?" in msg["content"]:
                            related_questions.append(msg["content"])
                    
                    # Ajouter au flux de conversation
                    conversation_flow.append(msg["content"])

                # Construire une requête enrichie basée sur l'analyse
                if recurring_topics:
                    # Mettre l'accent sur les thèmes récurrents
                    query_parts.append(f"Dans le contexte de la discussion sur {', '.join(recurring_topics)}")
                
                if related_questions:
                    # Inclure la continuité des questions
                    query_parts.append("En lien avec les questions précédentes:")
                    query_parts.extend(related_questions[-2:])  # Limiter aux 2 dernières questions pertinentes

                # Ajouter le contexte de flux conversationnel
                query_parts.append("Contexte de la conversation:")
                query_parts.extend(conversation_flow)

            # Intégrer le contexte du salon de manière plus ciblée
            if room_context and room_context.shared_context:
                relevant_context = {}
                for k, v in room_context.shared_context.items():
                    # Vérifier la pertinence par rapport aux thèmes identifiés
                    context_topics = self._extract_topics([k, v])
                    if context_topics & query_topics:
                        relevance_score = len(context_topics & query_topics) / len(query_topics)
                        if relevance_score > 0.3:  # Seuil de pertinence
                            relevant_context[k] = v

                if relevant_context:
                    query_parts.append("Contexte spécifique pertinent:")
                    for k, v in relevant_context.items():
                        query_parts.append(f"{k}: {v}")

            # Ajouter la requête originale à la fin
            query_parts.append(f"Question actuelle: {query}")

        except Exception as e:
            logger.warning(f"Erreur enrichissement requête: {str(e)}")
            return query

        # Joindre les parties avec des poids différents
        enhanced_query = " ".join(query_parts)
        
        # Limiter la longueur pour éviter une surcharge
        if len(enhanced_query) > 1000:
            # Garder les parties les plus importantes
            return f"{enhanced_query[:500]}... {query}"
            
        return enhanced_query

    def _is_relevant_to_query(self, context_key: str, query: str) -> bool:
        """Détermine si un élément de contexte est pertinent pour la requête"""
        # Liste de mots-clés importants de la requête
        query_keywords = set(query.lower().split())
        context_keywords = set(context_key.lower().split('_'))
        
        # Vérifier le chevauchement des mots-clés
        return bool(query_keywords & context_keywords)

    async def _process_chunks_for_conversation(
        self,
        chunks: List[Dict],
        session_context: Optional[SessionContext],
        room_context: Optional[RoomContext]
    ) -> List[Dict]:
        """Traite et enrichit les chunks pour le contexte conversationnel"""
        if not chunks:
            return []

        # Extraire les thèmes actifs de la conversation
        active_topics = set()
        likely_next_topics = set()
        if session_context and room_context:
            active_topics = self.conversation_history.get_active_topics(room_context.room_id)
            likely_next_topics = self.conversation_history.get_likely_next_topics(active_topics)

        # Traiter chaque chunk
        for chunk in chunks:
            metadata = chunk.get("metadata", {})
            content = chunk.get("content", "")
            
            # Score de base
            base_score = metadata.get("score", 0.0)
            
            # Calculer le boost contextuel
            context_boost = 0.0
            
            # 1. Boost basé sur la continuité thématique
            chunk_topics = self._extract_topics([content])
            topic_overlap = len(chunk_topics & active_topics)
            if topic_overlap > 0:
                context_boost += 0.2 * (topic_overlap / len(active_topics))
            
            # 2. Boost basé sur les topics probables suivants
            next_topic_overlap = len(chunk_topics & likely_next_topics)
            if next_topic_overlap > 0:
                context_boost += 0.1 * (next_topic_overlap / len(likely_next_topics))
            
            # 3. Boost basé sur la récence
            if "timestamp" in metadata:
                time_diff = time.time() - metadata["timestamp"]
                recency_boost = max(0, 0.1 * (1 - (time_diff / (24 * 3600))))
                context_boost += recency_boost
            
            # 4. Boost basé sur la pertinence pour le salon
            if room_context and room_context.shared_context:
                room_topics = self._extract_topics(room_context.shared_context.values())
                room_overlap = len(chunk_topics & room_topics)
                if room_overlap > 0:
                    context_boost += 0.1 * (room_overlap / len(room_topics))
            
            # Mettre à jour les scores
            metadata["context_boost"] = context_boost
            metadata["final_score"] = base_score * (1 + context_boost)
            chunk["metadata"] = metadata

        # Trier par score final
        chunks.sort(key=lambda x: x.get("metadata", {}).get("final_score", 0), reverse=True)
        
        return chunks

    def _extract_topics(self, texts: List[str]) -> Set[str]:
        """Extrait les sujets clés d'une liste de textes"""
        # Implémentation simple basée sur les mots-clés
        topics = set()
        stop_words = {"le", "la", "les", "un", "une", "des", "ce", "ces", "est", "sont"}
        
        for text in texts:
            # Tokenisation simple
            words = text.lower().split()
            # Filtrer les mots courts et les stop words
            keywords = {w for w in words if len(w) > 3 and w not in stop_words}
            topics.update(keywords)
        
        return topics

    async def generate_response(
        self,
        query: str,
        chunks: List[Dict],
        session_context: Optional[SessionContext] = None
    ) -> str:
        """
        Génère une réponse basée sur la requête et les chunks pertinents.
        
        Args:
            query: La requête de l'utilisateur
            chunks: Les chunks de documents pertinents
            session_context: Le contexte de session optionnel
            
        Returns:
            La réponse générée
        """
        if not query or not query.strip():
            logger.warning("Requête vide, impossible de générer une réponse appropriée")
            return "Je ne peux pas répondre à une requête vide. Pourriez-vous reformuler votre question ?"
        
        try:
            # Préparation du système prompt
            system_prompt = self._build_system_prompt(chunks)
            
            # Préparation des messages
            messages = []
            
            # Ajout du message système
            messages.append({
                "role": "system",
                "content": system_prompt
            })
            
            # Ajout de l'historique de conversation si disponible
            if session_context and hasattr(session_context, "history") and session_context.history:
                # Limiter l'historique aux derniers échanges pour éviter de dépasser les limites de tokens
                limited_history = session_context.history[-8:]
                
                for msg in limited_history:
                    if msg.get("content") and msg.get("role") in ["user", "assistant"]:
                        messages.append({
                            "role": msg["role"],
                            "content": msg["content"]
                        })
            
            # Ajout de la requête actuelle
            messages.append({
                "role": "user",
                "content": query
            })
            
            # Générer la réponse
            logger.info("Génération de la réponse avec Albert...")
            
            try:
                # Tentative avec l'historique complet
                response = await generate(self.config, messages)
            except Exception as full_err:
                logger.warning(f"Échec de génération avec l'historique complet: {str(full_err)}")
                
                try:
                    # Réessayer sans l'historique en cas d'erreur
                    simplified_messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query}
                    ]
                    logger.info("Tentative de génération sans historique...")
                    response = await generate(self.config, simplified_messages)
                except Exception as simple_err:
                    logger.error(f"Échec de génération même sans historique: {str(simple_err)}")
                    
                    # Dernière tentative sans chunks
                    try:
                        minimal_messages = [
                            {"role": "system", "content": "Vous êtes Colaig, l'assistant de l'État français."},
                            {"role": "user", "content": query}
                        ]
                        logger.info("Tentative de génération minimale...")
                        response = await generate(self.config, minimal_messages)
                    except Exception as minimal_err:
                        logger.error(f"Échec de génération minimale: {str(minimal_err)}")
                        # Réponse de secours
                        response = "Je suis désolé, mais je ne peux pas traiter votre demande pour le moment. Le service est temporairement indisponible ou surchargé. Veuillez réessayer ultérieurement."
            
            # Traitement post-génération
            if not response or len(response.strip()) < 5:
                logger.warning("Réponse vide ou trop courte reçue de l'API")
                response = "Je suis désolé, mais je n'ai pas pu générer une réponse adéquate à votre question. Pourriez-vous reformuler ou préciser votre demande ?"
            
            return response
            
        except Exception as e:
            logger.error(f"Erreur lors de la génération de la réponse: {str(e)}")
            return "Je suis désolé, une erreur est survenue lors du traitement de votre demande. Veuillez réessayer ou contacter l'administrateur si le problème persiste."

    def _format_conversational_prompt(
        self,
        query: str,
        chunks: List[Dict],
        session_context: Optional[SessionContext]
    ) -> str:
        """Formate le prompt pour une réponse directe"""
        prompt_parts = []

        # Ajouter uniquement le contexte essentiel
        if session_context and session_context.history:
            recent_history = session_context.history[-2:]  # Limiter à 2 messages
            for msg in recent_history:
                role = "User" if msg["role"] == "user" else "Assistant"
                prompt_parts.append(f"{role}: {msg['content']}")
            prompt_parts.append("")

        # Ajouter la question
        prompt_parts.append(f"User: {query}")
        prompt_parts.append("")

        # Ajouter les documents de manière plus concise
        prompt_parts.append("Context:")
        for chunk in chunks:
            prompt_parts.append(chunk.get('content', ''))

        prompt_parts.append("")
        prompt_parts.append("Assistant: ")

        return "\n".join(prompt_parts)

    def _get_system_prompt(self, session_context: Optional[SessionContext]) -> str:
        """Génère un prompt système adapté au contexte"""
        prompt_parts = [
            "Vous êtes Colaig, l'assistant de l'État français. ",
            "Répondez de manière directe et concise aux questions, ",
            "en vous basant sur les documents fournis. ",
            "Ne mentionnez pas les documents ou le processus de recherche dans votre réponse."
        ]
        
        if session_context and session_context.history:
            conversation_style = self._analyze_conversation_style(session_context.history)
            prompt_parts.append(f"Adoptez un ton {conversation_style}.")
        
        return " ".join(prompt_parts)

    def _analyze_conversation_style(self, history: List[Dict]) -> str:
        """Analyse le style de la conversation pour adapter le ton"""
        # Analyse simple du style basée sur les messages récents
        formal_indicators = 0
        informal_indicators = 0

        for msg in history:
            if msg["role"] == "user":
                content = msg["content"].lower()
                # Indicateurs de style formel
                if any(w in content for w in ["pourriez-vous", "s'il vous plaît", "merci"]):
                    formal_indicators += 1
                # Indicateurs de style informel
                if any(w in content for w in ["salut", "hey", "ok", "cool"]):
                    informal_indicators += 1

        return "formel" if formal_indicators > informal_indicators else "plus décontracté"

    def _format_conversational_response(
        self,
        response: str,
        chunks: List[Dict]
    ) -> str:
        """Formate la réponse de manière concise"""
        # Nettoyer la réponse des mentions de documents ou de processus
        clean_response = response.replace("Basé sur les documents fournis, ", "")
        clean_response = clean_response.replace("D'après les documents, ", "")
        clean_response = clean_response.replace("Selon les sources, ", "")
        
        # Supprimer les mentions de réflexion ou de processus
        clean_response = clean_response.split("En regardant les documents")[0]
        clean_response = clean_response.split("En analysant les sources")[0]
        clean_response = clean_response.split("Je vais essayer de")[0]
        
        # Garder uniquement la partie pertinente de la réponse
        relevant_parts = []
        for line in clean_response.split("\n"):
            if not any(marker in line.lower() for marker in [
                "je me base", "je me suis basé", "sources consultées",
                "documents pertinents", "selon l'index", "d'après l'historique"
            ]):
                relevant_parts.append(line)
        
        # Reconstruire la réponse nettoyée
        clean_response = "\n".join(line for line in relevant_parts if line.strip())
        
        # Ajouter les sources uniquement si configuré
        if self.config.colaig_show_sources:
            sources = []
            for chunk in chunks:
                metadata = chunk.get("metadata", {})
                source_parts = [f"📄 {metadata.get('document_name', 'Document inconnu')}"]
                
                if metadata.get("section_title"):
                    source_parts.append(f"   Section: {metadata['section_title']}")
                    
                sources.append("\n".join(source_parts))
            
            if sources:
                clean_response += "\n\n💡 Sources :\n" + "\n".join(sources)
        
        # Ajouter uniquement l'emoji au début
        return f"🤖 {clean_response.strip()}"

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.webdav_service:
                await self.webdav_service.__aexit__(None, None, None)
            if self.index_service:
                await self.index_service.__aexit__(None, None, None)
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture des services: {str(e)}")
            raise 