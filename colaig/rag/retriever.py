"""
Colaig — Recherche documentaire hybride

Implémente RetrieverProtocol.

Pipeline complet :
1. (optionnel) HyDE — LLM génère une réponse hypothétique → embedding combiné
2. FAISS dense search (k*2 candidats)
3. (optionnel) BM25 lexical search (k*2 candidats) → RRF fusion
4. MMR reranking pour diversité
5. (optionnel) Reranking cross-encoder Albert
6. Filtrage par score minimum
7. Top-k résultats
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from colaig.models import SearchResult

if TYPE_CHECKING:
    from colaig.integrations.albert import AlbertClient

logger = logging.getLogger(__name__)

# Paramètre MMR : 0 = max diversité, 1 = max pertinence
MMR_LAMBDA = 0.7

# Prompt HyDE
_HYDE_SYSTEM = "Tu es un expert documentaire. Génère une réponse concise et factuelle."
_HYDE_USER_TEMPLATE = """Question : {query}

Rédige une réponse documentaire hypothétique (2-3 phrases) comme si tu avais trouvé la réponse dans un document.
Réponds directement, sans introduction."""



def _deduplique_les_passages(candidats: list) -> list:
    """Retire les passages dont le TEXTE est deja servi, en gardant le mieux classe.

    POURQUOI, MESURE SUR LE CORPUS REEL LE 30/08/2026. L'espace SST contient 60
    documents dont 18 sont des copies exactes de 12 autres — deux arborescences se
    recouvrent, et l'un des fichiers s'appelle « … - Copie.odt ». Sur 120 requetes :

        k= 3   requetes touchees 20 %   places evincees 7 %
        k= 5   requetes touchees 22 %   places evincees 8 %
        k=10   requetes touchees 32 %   places evincees 9 %

    Une requete sur cinq rendait le meme passage deux fois, au detriment d'un passage
    qui aurait pu etre pertinent.

    CE QUI EST UN DOUBLON, ET CE QUI N'EN EST PAS. Le meme TEXTE a deux chemins en est
    un. Deux passages differents du meme document n'en sont pas — un document long et
    pertinent doit pouvoir repondre deux fois. Une premiere mesure confondait les deux
    et annoncait 68 % ; elle groupait par document au lieu de comparer les textes.

    La comparaison ignore les blancs : deux copies d'un fichier peuvent differer d'un
    espace ou d'un retour a la ligne apres conversion, sans que rien ne les distingue
    a la lecture.

    POURQUOI ICI ET NON A L'INDEXATION. Refuser d'indexer un doublon obligerait
    l'espace a etre propre. Un espace reel ne l'est pas, et le traverser quand meme est
    precisement ce que Colaig doit savoir faire : l'utilisateur depose ce qu'il a. On
    compose avec le desordre plutot que de l'interdire — local, reversible, sans
    reindexation.
    """
    vus: set[str] = set()
    gardes = []
    for r in candidats:
        empreinte = " ".join((getattr(r.chunk, "text", "") or "").split())
        if empreinte in vus:
            continue
        vus.add(empreinte)
        gardes.append(r)
    return gardes


class Retriever:
    """Service de recherche documentaire hybride.

    Args:
        embedding_service: Service d'embeddings (EmbeddingServiceProtocol).
        store: Index vectoriel FAISS (VectorStoreProtocol). Peut être changé via set_store().
        albert_client: Client LLM optionnel (reranking cross-encoder + HyDE).
        hyde_enabled: Activer HyDE query expansion (nécessite albert_client).
        hyde_query_weight: Poids du vecteur HyDE dans la combinaison (0→1).
        rrf_k_constant: Constante k du Reciprocal Rank Fusion (défaut : 60).
    """

    def __init__(
        self,
        embedding_service,
        store=None,
        albert_client: AlbertClient | None = None,
        hyde_enabled: bool = False,
        hyde_query_weight: float = 0.5,
        rrf_k_constant: int = 60,
    ) -> None:
        self._embeddings = embedding_service  # EmbeddingServiceProtocol
        self._store = store  # VectorStoreProtocol | None
        self._albert_client: AlbertClient | None = albert_client
        self._hyde_enabled = hyde_enabled
        self._hyde_query_weight = hyde_query_weight
        self._rrf_k = rrf_k_constant

    def set_store(self, store) -> None:
        """Configure le VectorStore à utiliser (changement de workspace)."""
        self._store = store

    async def retrieve_many(
        self,
        queries: list[str],
        k: int = 5,
        score_threshold: float = 0.3,
        store=None,
        bm25_store=None,
    ) -> list[list[SearchResult]]:
        """Recherche pour PLUSIEURS requêtes, en UNE seule vectorisation.

        POURQUOI (L3.5)
        ----------------
        L'Analyseur produit 2 a 3 reformulations d'une meme question, plus des
        `document_queries`. `PreExecutionBuilder` bouclait dessus en appelant
        `retrieve()` a chaque fois : deux a six allers-retours reseau par tour, sur le
        chemin du message, avant que l'utilisateur voie quoi que ce soit.

        `embed_texts` existait deja, et l'indexation s'en servait pour des centaines de
        chunks. La brique du groupage etait la ; elle n'etait pas employee ici.

        CE QUI EST GROUPE, ET CE QUI NE L'EST PAS
        -------------------------------------------
        Seule la VECTORISATION est groupee — c'est le seul aller-retour reseau de
        l'etape. La recherche FAISS, le RRF et le MMR restent par requete : ils sont
        locaux, et les grouper ne gagnerait rien tout en changeant les resultats.

        HyDE retombe sur la boucle d'origine : il demande une GENERATION par requete,
        ce qui releve d'un autre arbitrage. Un test epingle cette limite pour qu'on ne
        croie pas le gain acquis partout.

        Returns:
            Une liste de resultats PAR requete, dans l'ordre des requetes.
        """
        if not queries:
            return []

        effective_store = store if store is not None else self._store
        if effective_store is None or effective_store.count == 0:
            # Meme court-circuit que `retrieve` : interroger un index vide ne vaut pas
            # un aller-retour reseau.
            return [[] for _ in queries]

        if self._hyde_enabled and self._albert_client is not None:
            return [await self.retrieve(q, k=k, score_threshold=score_threshold,
                                        store=store, bm25_store=bm25_store)
                    for q in queries]

        vecteurs = await self._embeddings.embed_texts(queries)
        return [
            await self.retrieve(q, k=k, score_threshold=score_threshold,
                                store=store, bm25_store=bm25_store,
                                query_embedding=v)
            for q, v in zip(queries, vecteurs)
        ]

    async def retrieve(
        self,
        query: str,
        k: int = 5,
        score_threshold: float = 0.3,
        store=None,
        bm25_store=None,
        query_embedding: list[float] | None = None,
    ) -> list[SearchResult]:
        """Recherche les documents les plus pertinents.

        Pipeline:
        1. (optionnel) HyDE : génère embedding enrichi
        2. FAISS search dense (k * 2 candidats)
        3. (optionnel) BM25 search + RRF fusion
        4. MMR reranking
        5. (optionnel) Reranking cross-encoder Albert
        6. Filtrage par score minimum
        7. Top-k

        Args:
            query: Texte de la requête.
            k: Nombre de résultats à retourner.
            score_threshold: Score minimum pour inclure un résultat (sans reranker Albert).
            store: VectorStore spécifique (isolation workspace). Si None, utilise self._store.
            query_embedding: vecteur déjà calculé. Fourni, l'étape 1 est sautée — c'est
                ce qui permet à `retrieve_many` de grouper la vectorisation sans
                dupliquer le reste du pipeline. Aucun appelant existant ne le passe.
            bm25_store: BM25Store spécifique (hybrid search). Si None, désactive BM25.

        Returns:
            Liste de SearchResult triés par pertinence.
        """
        effective_store = store if store is not None else self._store

        if effective_store is None:
            logger.warning("retriever: pas de store configuré")
            return []

        if effective_store.count == 0:
            logger.warning("retriever: store vide (workspace pas encore indexé ?)")
            return []

        # 1. Embed la query (+ expansion HyDE optionnelle)
        if query_embedding is None:
            query_embedding = await self._embeddings.embed_text(query)
            if self._hyde_enabled and self._albert_client is not None:
                query_embedding = await self._hyde_expand_query(query, query_embedding)

        # 2. FAISS search — 2x plus de candidats pour le reranking
        candidates = effective_store.search(query_embedding, k=k * 2)
        if not candidates:
            return []

        # 3. BM25 + RRF (hybrid search)
        if bm25_store is not None and bm25_store.count > 0:
            bm25_results = bm25_store.search(query, k=k * 2)
            candidates = _rrf_combine(candidates, bm25_results, k=k * 2, k_constant=self._rrf_k)

        # 3b. Le meme passage ne prend pas deux places.
        # AVANT le MMR : celui-ci reduit le vivier a , donc dedupliquer apres
        # laisserait un trou la ou l'on veut un passage de plus.
        candidates = _deduplique_les_passages(candidates)

        # 4. MMR reranking
        reranked = _mmr_rerank(candidates, query_embedding, k=k, lambda_param=MMR_LAMBDA)

        # 4b. Reranking cross-encoder Albert (optionnel)
        albert_reranked = False
        if self._albert_client is not None and len(reranked) > 1:
            faiss_scores = [round(r.score, 4) for r in reranked[:3]]
            logger.info("retriever: FAISS top scores avant rerank: %s", faiss_scores)
            reranked = await self._albert_rerank(query, reranked)
            albert_reranked = True

        # 5. Filtrage par score
        # Threshold adaptatif : HyDE produit des embeddings enrichis → similarités plus élevées
        # → le seuil par défaut 0.3 peut être trop conservateur. Avec reranker Albert
        # (BAAI/bge-reranker-v2-m3), les scores sigmoid sont ~0.001-0.005.
        if albert_reranked:
            effective_threshold = 0.001
        elif self._hyde_enabled and self._albert_client is not None:
            effective_threshold = max(score_threshold - 0.05, 0.0)  # HyDE → seuil légèrement abaissé
        else:
            effective_threshold = score_threshold
        filtered = [r for r in reranked if r.score >= effective_threshold]

        # 6. Budget tokens : éviter de dépasser la fenêtre de contexte Albert
        # Estimation : 4 chars ≈ 1 token. Budget cible : 6000 tokens pour les documents.
        TOKEN_BUDGET = 6000
        budgeted: list[SearchResult] = []
        token_count = 0
        for r in filtered:
            estimated_tokens = len(r.chunk.text) // 4
            if token_count + estimated_tokens > TOKEN_BUDGET:
                logger.debug("retriever: budget tokens atteint (%d tokens), %d chunks tronqués",
                             token_count, len(filtered) - len(budgeted))
                break
            budgeted.append(r)
            token_count += estimated_tokens
        filtered = budgeted

        # 7. Renuméroter les rangs
        for i, result in enumerate(filtered):
            result.rank = i

        reranker_used = "albert" if albert_reranked else ("rrf+mmr" if bm25_store else "mmr")

        if not filtered and reranked:
            # Aider le diagnostic : montrer les scores réels quand tout est filtré
            top_scores = [round(r.score, 3) for r in reranked[:3]]
            logger.warning(
                "retriever: 0 résultat (threshold=%.2f, reranker=%s). Top scores avant filtrage: %s",
                effective_threshold, reranker_used, top_scores,
            )
        else:
            logger.debug(
                "retriever: %d candidats → %d après %s → %d après filtrage (threshold=%.2f)",
                len(candidates), len(reranked), reranker_used, len(filtered), score_threshold,
            )

        return filtered[:k]

    async def _hyde_expand_query(
        self,
        query: str,
        query_embedding: list[float],
    ) -> list[float]:
        """HyDE : génère une réponse hypothétique et combine son embedding avec la query.

        Hypothetical Document Embeddings (Gao et al., 2022) :
        un LLM génère une réponse hypothétique à la question → l'embedding de cette réponse
        est souvent plus proche des documents pertinents que l'embedding de la question seule.

        Args:
            query: Texte de la requête.
            query_embedding: Embedding original de la requête.

        Returns:
            Embedding combiné (query + hyde), ou query_embedding original si erreur.
        """
        try:
            messages = [{"role": "user", "content": _HYDE_USER_TEMPLATE.format(query=query)}]
            hypothetical = await self._albert_client.chat(
                messages=messages,
                system_prompt=_HYDE_SYSTEM,
                max_tokens=200,
            )
            if not hypothetical.strip():
                return query_embedding

            hyde_embedding = await self._embeddings.embed_text(hypothetical.strip())

            # Combinaison linéaire : (1-w)*query + w*hyde
            w = self._hyde_query_weight
            q = np.array(query_embedding, dtype=np.float32)
            h = np.array(hyde_embedding, dtype=np.float32)
            combined = (1 - w) * q + w * h

            # Normaliser L2
            norm = np.linalg.norm(combined)
            if norm > 0:
                combined = combined / norm

            logger.debug("hyde: réponse hypothétique générée (%d chars)", len(hypothetical))
            return combined.tolist()

        except Exception:
            logger.warning("hyde: erreur génération, fallback query originale", exc_info=True)
            return query_embedding

    async def _albert_rerank(
        self,
        query: str,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        """Reranke les résultats MMR via le cross-encoder Albert.

        Remplace les scores FAISS/RRF par les scores du reranker et retrie.
        En cas d'erreur, retourne l'ordre MMR inchangé.
        """
        try:
            texts = [r.chunk.text for r in results]
            ranked_pairs = await self._albert_client.rerank(query, texts, top_n=len(texts))

            # UN RERANKER ABSENT NE DOIT PAS EFFACER LA RECHERCHE.
            #
            # Le contrat est ecrit dans `colaig/integrations/CLAUDE.md` : « rerank
            # retourne [] si le provider ne supporte pas (404/405) → l'appelant peut
            # utiliser MMR comme fallback ». L'appelant ne retombait pas : la boucle
            # ci-dessous ne tournait pas, et l'on rendait une liste VIDE.
            #
            # Le `except` n'attrapait rien, une liste vide n'etant pas une erreur.
            #
            # `OpenAIClient` — donc SSPCloud, la CIBLE DE PRODUCTION — n'expose pas
            # d'endpoint de reranking. Sur cette pile, TOUTE recherche documentaire
            # rendait zero resultat, sans erreur ni avertissement : Colaig repondait de
            # memoire en paraissant fonctionner. Releve a la premiere question posee a
            # un corpus reel, le 30/08/2026 :
            #
            #     FAISS top scores avant rerank: [0.7188, 0.7188, 0.7188]
            #     reranker Albert scores: []
            #     echange … sources=[] confiance=0.00
            if not ranked_pairs:
                logger.info("reranking non disponible — ordre MMR conserve (%d résultats)",
                            len(results))
                return results

            logger.info(
                "reranker Albert scores: %s",
                [(round(s, 4), results[i].chunk.source_name or results[i].chunk.source_path[:30]) for i, s in ranked_pairs],
            )
            reordered = []
            for orig_idx, score in ranked_pairs:
                result = results[orig_idx]
                result.score = score
                reordered.append(result)
            return reordered
        except Exception:
            logger.warning("reranking Albert échoué, fallback ordre MMR", exc_info=True)
            return results


# ── Fonctions utilitaires ────────────────────────────────────────────────────


def _chunk_key(result: SearchResult) -> tuple:
    """Clé unique d'un chunk pour le matching inter-index (FAISS ↔ BM25)."""
    c = result.chunk
    return (c.source_path, c.position, c.section)


def _rrf_combine(
    faiss_results: list[SearchResult],
    bm25_results: list[tuple],
    k: int,
    k_constant: int = 60,
) -> list[SearchResult]:
    """Reciprocal Rank Fusion — fusionne FAISS dense et BM25 lexical.

    score_RRF(doc) = Σ 1 / (k_constant + rank(doc, index))

    Args:
        faiss_results: Résultats FAISS (SearchResult), triés par score décroissant.
        bm25_results: Résultats BM25 (list of (DocumentChunk, score)), triés par score desc.
        k: Nombre de résultats combinés à retourner.
        k_constant: Constante k (défaut 60, recommandation littérature).

    Returns:
        Liste de SearchResult triés par score RRF décroissant.
    """
    rrf_scores: dict[tuple, float] = {}
    # Mapping clé → SearchResult (FAISS prioritaire pour les métadonnées)
    key_to_result: dict[tuple, SearchResult] = {}

    # Contributions FAISS
    for rank, result in enumerate(faiss_results):
        key = _chunk_key(result)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k_constant + rank + 1)
        key_to_result[key] = result

    # Contributions BM25
    for rank, (chunk, _score) in enumerate(bm25_results):
        from colaig.models import SearchResult as SR
        key = (chunk.source_path, chunk.position, chunk.section)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k_constant + rank + 1)
        if key not in key_to_result:
            # Chunk présent seulement dans BM25 — créer un SearchResult pour lui
            key_to_result[key] = SR(chunk=chunk, score=0.0, rank=0)

    # Trier par score RRF décroissant
    sorted_keys = sorted(rrf_scores.keys(), key=lambda kk: rrf_scores[kk], reverse=True)

    results: list[SearchResult] = []
    for key in sorted_keys[:k]:
        result = key_to_result[key]
        result.score = rrf_scores[key]
        results.append(result)

    return results


def _mmr_rerank(
    candidates: list[SearchResult],
    query_embedding: list[float],
    k: int,
    lambda_param: float = 0.7,
) -> list[SearchResult]:
    """Maximum Marginal Relevance reranking.

    Équilibre pertinence (similarité à la query) et diversité
    (dissimilarité avec les résultats déjà sélectionnés).

    score_MMR = λ * sim(doc, query) - (1-λ) * max(sim(doc, selected))
    """
    if len(candidates) <= 1:
        return candidates

    # PERTINENCE NORMALISÉE — sans quoi λ ne veut rien dire.
    #
    # Le score d'un candidat ne vient pas toujours de la même échelle. FAISS rend une
    # cosinus dans [0, 1] ; RRF rend `1 / (60 + rang)`, soit **0,016 au premier rang**.
    # Avec λ = 0,7, le terme de pertinence pesait alors 0,011 face à un terme de
    # diversité allant jusqu'à 0,3 — deux ordres de grandeur d'écart. Après fusion,
    # MMR ne classait donc plus par pertinence pondérée de diversité : il retenait le
    # plus dissemblable, la pertinence étant numériquement invisible.
    #
    # Mesuré sur les 103 cas dorés porteurs d'articles attendus : `RRF + MMR k=6`
    # rendait **50/103** là où le dense seul rendait 88/103. Ce n'était pas un mauvais
    # réglage de λ, c'était une comparaison entre grandeurs incommensurables.
    #
    # La normalisation min-max rend `_mmr_rerank` **indifférent à l'échelle** de ce qui
    # le précède : λ garde le même sens derrière FAISS, derrière RRF, ou derrière tout
    # autre classement à venir.
    scores = [c.score for c in candidates]
    _lo, _hi = min(scores), max(scores)
    _etendue = _hi - _lo
    pertinence = {
        id(c): ((c.score - _lo) / _etendue) if _etendue > 0 else 1.0
        for c in candidates
    }

    selected: list[SearchResult] = []
    remaining = list(candidates)

    # Premier résultat = le plus pertinent
    selected.append(remaining.pop(0))

    # Pré-convertir les embeddings en numpy pour le calcul cosine
    # (les vecteurs sont déjà normalisés L2 — dot product = cosine similarity)
    _use_real_cosine = bool(candidates and candidates[0].embedding)

    while len(selected) < k and remaining:
        best_score = float("-inf")
        best_idx = 0

        for i, candidate in enumerate(remaining):
            # Pertinence ramenée dans [0, 1] — voir la note en tête de fonction.
            relevance = pertinence[id(candidate)]

            # Diversité : max similarité cosinus avec les chunks déjà sélectionnés
            max_sim_selected = 0.0
            for sel in selected:
                if _use_real_cosine and candidate.embedding and sel.embedding:
                    # Vraie cosine similarity (dot product entre vecteurs normalisés L2)
                    sim = float(np.dot(candidate.embedding, sel.embedding))
                    sim = max(0.0, min(1.0, sim))
                else:
                    # Fallback : pénalité basée sur la source (même doc → forte similarité)
                    sim = 0.8 if candidate.chunk.source_path == sel.chunk.source_path else 0.1
                max_sim_selected = max(max_sim_selected, sim)

            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim_selected
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        selected.append(remaining.pop(best_idx))

    return selected
