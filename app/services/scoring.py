from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from datetime import datetime
import statistics
from dataclasses import dataclass

from matrix_bot.config import logger

@dataclass
class ScoringResult:
    """Résultat du scoring pour un chunk"""
    base_score: float  # Score de similarité cosinus de base
    adjusted_score: float  # Score final ajusté
    context_boost: float  # Boost basé sur le contexte
    metadata_boost: float  # Boost basé sur les métadonnées
    chunk_id: str  # Identifiant du chunk

class DynamicScoreManager:
    """Gestionnaire de scoring dynamique pour les résultats de recherche"""
    
    def __init__(self):
        self.score_history: List[float] = []  # Historique des scores pour l'adaptation dynamique
        self.min_base_threshold = 0.35  # Seuil minimum absolu
        self.max_boost = 0.3  # Boost maximum total
        
    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        """Convertit une valeur en float de manière sécurisée"""
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            logger.warning(f"Impossible de convertir en float: {value}")
            return default
        
    def calculate_dynamic_threshold(self) -> float:
        """Calcule le seuil dynamique basé sur l'historique des scores"""
        if not self.score_history:
            return self.min_base_threshold
            
        # Utiliser les statistiques des derniers scores
        recent_scores = self.score_history[-100:]  # Limiter à 100 derniers scores
        
        try:
            mean = statistics.mean(recent_scores)
            stdev = statistics.stdev(recent_scores) if len(recent_scores) > 1 else 0
            
            # Ajuster le seuil dynamiquement
            dynamic_threshold = max(
                self.min_base_threshold,
                mean - (2 * stdev)  # 2 écarts-types sous la moyenne
            )
            
            return self._safe_float(dynamic_threshold, self.min_base_threshold)
            
        except Exception as e:
            logger.warning(f"Erreur calcul seuil dynamique: {str(e)}")
            return self.min_base_threshold
    
    def calculate_context_boost(self, chunk: Dict[str, Any], query: str) -> float:
        """Calcule le boost basé sur le contexte du document"""
        boost = 0.0
        
        try:
            metadata = chunk.get('metadata', {})
            
            # Boost pour les documents structurés
            if metadata.get('is_pdf', False):
                boost += 0.05
            
            # Boost pour les sections avec titre
            if metadata.get('section_title'):
                boost += 0.05
            
            # Boost pour les documents avec position/page
            if metadata.get('page') is not None:
                boost += 0.05
            
            # Boost pour les chunks avec contexte adjacent
            if metadata.get('adjacent_sections', {}).get('previous') or \
               metadata.get('adjacent_sections', {}).get('next'):
                boost += 0.05
            
            return self._safe_float(min(boost, 0.15))  # Maximum 0.15 pour le boost contextuel
            
        except Exception as e:
            logger.warning(f"Erreur calcul boost contextuel: {str(e)}")
            return 0.0
    
    def calculate_metadata_boost(self, chunk: Dict[str, Any], query: str) -> float:
        """Calcule le boost basé sur les métadonnées"""
        boost = 0.0
        
        try:
            metadata = chunk.get('metadata', {})
            
            # Boost pour les documents récents
            if metadata.get('last_updated'):
                try:
                    last_updated = datetime.fromisoformat(str(metadata['last_updated']))
                    age_days = (datetime.now() - last_updated).days
                    if age_days < 30:  # Document de moins d'un mois
                        boost += 0.1 * (1 - (age_days / 30))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Erreur parsing date: {str(e)}")
            
            # Boost pour les documents avec métadonnées riches
            metadata_completeness = sum(1 for k, v in metadata.items() if v is not None)
            boost += min(0.05 * (metadata_completeness / 10), 0.1)
            
            return self._safe_float(min(boost, 0.15))  # Maximum 0.15 pour le boost métadonnées
            
        except Exception as e:
            logger.warning(f"Erreur calcul boost métadonnées: {str(e)}")
            return 0.0
    
    def adjust_scores(
        self,
        chunks: List[Dict[str, Any]],
        query: str,
        base_similarities: np.ndarray
    ) -> List[ScoringResult]:
        """Ajuste les scores des chunks en fonction du contexte et des métadonnées
        
        Args:
            chunks: Liste des chunks à scorer
            query: Requête de recherche
            base_similarities: Scores de similarité cosinus de base
            
        Returns:
            Liste des résultats de scoring
        """
        results = []
        
        # Calculer le seuil dynamique
        threshold = self.calculate_dynamic_threshold()
        
        for chunk, base_score in zip(chunks, base_similarities):
            try:
                # Convertir le score de base en float de manière sécurisée
                base_score_float = self._safe_float(base_score)
                
                # Ignorer les scores trop faibles
                if base_score_float < threshold:
                    continue
                    
                # Calculer les boosts
                context_boost = self.calculate_context_boost(chunk, query)
                metadata_boost = self.calculate_metadata_boost(chunk, query)
                
                # Calculer le score final
                total_boost = min(context_boost + metadata_boost, self.max_boost)
                adjusted_score = base_score_float * (1 + total_boost)
                
                # Créer le résultat
                result = ScoringResult(
                    base_score=base_score_float,
                    adjusted_score=self._safe_float(adjusted_score),
                    context_boost=context_boost,
                    metadata_boost=metadata_boost,
                    chunk_id=str(chunk['id'])  # Assurer que l'ID est une chaîne
                )
                results.append(result)
                
                # Mettre à jour l'historique
                self.score_history.append(adjusted_score)
                if len(self.score_history) > 1000:  # Limiter la taille de l'historique
                    self.score_history = self.score_history[-1000:]
                    
            except Exception as e:
                logger.warning(f"Erreur traitement chunk {chunk.get('id')}: {str(e)}")
                continue
        
        # Trier par score ajusté
        results.sort(key=lambda x: x.adjusted_score, reverse=True)
        return results 