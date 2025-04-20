from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import numpy as np
from datetime import datetime

from app.matrix_bot.config import logger
from app.services.models import DocumentChunk

@dataclass
class ChunkContext:
    """Contexte d'un chunk pour l'analyse"""
    chunk: DocumentChunk
    previous_chunk: Optional[DocumentChunk]
    next_chunk: Optional[DocumentChunk]
    document_chunks: List[DocumentChunk]  # Tous les chunks du même document
    similarity_score: float  # Score de similarité avec la requête

@dataclass
class ChunkGroup:
    """Groupe de chunks connexes"""
    chunks: List[ChunkContext]
    avg_similarity: float
    document_path: str
    is_contiguous: bool

class ChunkSelector:
    """Gestionnaire de sélection intelligente des chunks"""
    
    def __init__(self):
        self.similarity_threshold = 0.1  # Seuil de similarité pour considérer deux chunks comme similaires
        self.max_group_size = 3  # Nombre maximum de chunks dans un groupe
        
    def get_chunk_context(
        self,
        chunk: DocumentChunk,
        all_chunks: List[DocumentChunk],
        similarity_score: float
    ) -> ChunkContext:
        """Construit le contexte d'un chunk"""
        # Récupérer tous les chunks du même document
        doc_chunks = [c for c in all_chunks if c.document_path == chunk.document_path]
        doc_chunks.sort(key=lambda x: x.chunk_number)
        
        # Trouver la position du chunk actuel
        try:
            current_idx = doc_chunks.index(chunk)
            prev_chunk = doc_chunks[current_idx - 1] if current_idx > 0 else None
            next_chunk = doc_chunks[current_idx + 1] if current_idx < len(doc_chunks) - 1 else None
        except ValueError:
            prev_chunk = None
            next_chunk = None
        
        return ChunkContext(
            chunk=chunk,
            previous_chunk=prev_chunk,
            next_chunk=next_chunk,
            document_chunks=doc_chunks,
            similarity_score=similarity_score
        )
    
    def calculate_chunk_similarity(self, chunk1: DocumentChunk, chunk2: DocumentChunk) -> float:
        """Calcule la similarité entre deux chunks basée sur leurs métadonnées"""
        score = 0.0
        
        # Similarité basée sur la proximité dans le document
        if chunk1.document_path == chunk2.document_path:
            chunk_distance = abs(chunk1.chunk_number - chunk2.chunk_number)
            if chunk_distance <= 1:
                score += 0.5
            elif chunk_distance <= 2:
                score += 0.3
        
        # Similarité basée sur les métadonnées communes
        common_metadata = 0
        if chunk1.metadata and chunk2.metadata:
            for key in ['section_title', 'document_title', 'page_number']:
                if chunk1.metadata.get(key) == chunk2.metadata.get(key):
                    common_metadata += 1
        score += 0.1 * common_metadata
        
        return min(score, 1.0)
    
    def group_related_chunks(
        self,
        chunk_contexts: List[ChunkContext]
    ) -> List[ChunkGroup]:
        """Regroupe les chunks connexes"""
        groups: List[ChunkGroup] = []
        processed_chunks = set()
        
        # Trier par score de similarité
        sorted_contexts = sorted(
            chunk_contexts,
            key=lambda x: x.similarity_score,
            reverse=True
        )
        
        for context in sorted_contexts:
            if context.chunk.id in processed_chunks:
                continue
                
            # Créer un nouveau groupe
            current_group = [context]
            processed_chunks.add(context.chunk.id)
            
            # Chercher les chunks connexes
            for other_context in sorted_contexts:
                if len(current_group) >= self.max_group_size:
                    break
                    
                if other_context.chunk.id in processed_chunks:
                    continue
                    
                # Vérifier la connexité
                is_connected = False
                for group_context in current_group:
                    similarity = self.calculate_chunk_similarity(
                        group_context.chunk,
                        other_context.chunk
                    )
                    if similarity > self.similarity_threshold:
                        is_connected = True
                        break
                
                if is_connected:
                    current_group.append(other_context)
                    processed_chunks.add(other_context.chunk.id)
            
            # Calculer les métriques du groupe
            avg_similarity = sum(c.similarity_score for c in current_group) / len(current_group)
            is_contiguous = all(
                abs(current_group[i].chunk.chunk_number - current_group[i-1].chunk.chunk_number) == 1
                for i in range(1, len(current_group))
            ) if len(current_group) > 1 else True
            
            groups.append(ChunkGroup(
                chunks=current_group,
                avg_similarity=avg_similarity,
                document_path=current_group[0].chunk.document_path,
                is_contiguous=is_contiguous
            ))
        
        return groups
    
    def select_best_chunks(
        self,
        chunks: List[DocumentChunk],
        similarity_scores: List[float],
        limit: int
    ) -> List[Tuple[DocumentChunk, float]]:
        """Sélectionne les meilleurs chunks en tenant compte du contexte
        
        Args:
            chunks: Liste des chunks candidats
            similarity_scores: Scores de similarité correspondants
            limit: Nombre maximum de chunks à retourner
            
        Returns:
            Liste des chunks sélectionnés avec leurs scores
        """
        try:
            # Créer les contextes pour chaque chunk
            contexts = [
                self.get_chunk_context(chunk, chunks, score)
                for chunk, score in zip(chunks, similarity_scores)
            ]
            
            # Grouper les chunks connexes
            groups = self.group_related_chunks(contexts)
            
            # Trier les groupes par score moyen
            groups.sort(key=lambda x: x.avg_similarity, reverse=True)
            
            # Sélectionner les meilleurs chunks
            selected_chunks: List[Tuple[DocumentChunk, float]] = []
            seen_documents = set()
            
            # D'abord, prendre le meilleur chunk de chaque groupe
            for group in groups:
                if len(selected_chunks) >= limit:
                    break
                    
                # Éviter la surreprésentation d'un même document
                if group.document_path in seen_documents and len(seen_documents) < limit:
                    continue
                    
                # Sélectionner le meilleur chunk du groupe
                best_context = max(group.chunks, key=lambda x: x.similarity_score)
                selected_chunks.append((best_context.chunk, best_context.similarity_score))
                seen_documents.add(group.document_path)
                
                # Si le groupe est contigu, ajouter les chunks adjacents
                if group.is_contiguous and len(selected_chunks) < limit:
                    remaining_contexts = sorted(
                        [c for c in group.chunks if c != best_context],
                        key=lambda x: x.similarity_score,
                        reverse=True
                    )
                    for context in remaining_contexts:
                        if len(selected_chunks) >= limit:
                            break
                        selected_chunks.append((context.chunk, context.similarity_score))
            
            # Compléter avec les meilleurs chunks restants si nécessaire
            if len(selected_chunks) < limit:
                remaining_contexts = sorted(
                    [c for c in contexts if c.chunk not in [x[0] for x in selected_chunks]],
                    key=lambda x: x.similarity_score,
                    reverse=True
                )
                for context in remaining_contexts:
                    if len(selected_chunks) >= limit:
                        break
                    selected_chunks.append((context.chunk, context.similarity_score))
            
            return selected_chunks
            
        except Exception as e:
            logger.error(f"Erreur lors de la sélection des chunks: {str(e)}")
            # Fallback : retourner les premiers chunks triés par score
            sorted_chunks = sorted(
                zip(chunks, similarity_scores),
                key=lambda x: x[1],
                reverse=True
            )
            return sorted_chunks[:limit] 