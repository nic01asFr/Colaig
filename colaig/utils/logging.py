"""
Colaig — Configuration du logging structuré

Utilise structlog pour produire des logs JSON en production
et des logs colorés lisibles en développement.

Usage::

    from colaig.utils.logging import setup_logging, get_logger

    setup_logging("DEBUG")
    logger = get_logger("colaig.rag.retriever")
    logger.info("recherche lancée", query="procédure validation", k=5)
"""

from __future__ import annotations

import json
import logging
import sys

import structlog


def setup_logging(level: str = "INFO") -> None:
    """Configure le logging structuré pour toute l'application.

    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Processeurs communs structlog
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # En mode DEBUG → logs colorés pour la console
    # Sinon → logs JSON pour la production
    if numeric_level <= logging.DEBUG:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer(
            serializer=lambda obj, **kw: json.dumps(obj, ensure_ascii=False, **kw)
        )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configuration du logging stdlib (pour les bibliothèques tierces)
    # foreign_pre_chain s'applique aux records stdlib (logging.getLogger) avant le renderer,
    # pour leur ajouter timestamp, level, logger_name — même format que les loggers structlog.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)

    # Bibliothèques tierces — toujours WARNING minimum
    for noisy_logger in ("httpx", "httpcore", "urllib3", "matrix_nio", "hpack", "asyncio",
                         "peewee", "aiohttp", "aiosqlite", "multipart"):
        logging.getLogger(noisy_logger).setLevel(max(numeric_level, logging.WARNING))
    logging.getLogger("nio").setLevel(logging.CRITICAL)
    logging.getLogger("nio.store").setLevel(logging.CRITICAL)

    # Modules internes verbeux en DEBUG — on les bride à INFO pour garder les logs lisibles
    # (chunker, FAISS, embeddings sont utiles en DEBUG ponctuel, pas en continu)
    if numeric_level <= logging.DEBUG:
        for internal_logger in (
            "colaig.rag.chunker",
            "colaig.rag.faiss_store",
            "colaig.rag.embeddings",
            "colaig.rag.retriever",
            "colaig.rag.indexer",
            "colaig.storage.cache",
        ):
            logging.getLogger(internal_logger).setLevel(logging.INFO)

    installer_masquage_secrets()


def installer_masquage_secrets() -> None:
    """Pose le masquage des secrets sur les HANDLERS de la racine, pas sur le logger.

    POURQUOI PAS `logging.getLogger().addFilter(...)`
    --------------------------------------------------
    C'est ce que faisait la version precedente, et **elle ne protegeait rien**.

    Un filtre attache a un LOGGER ne s'applique qu'aux enregistrements emis
    DIRECTEMENT sur lui. Ceux qui remontent depuis ses enfants atteignent bien ses
    handlers, mais pas ses filtres.

    Or toute l'application journalise depuis des loggers nommes —
    `colaig.rag.generator`, `colaig.messaging.matrix`... Aucun n'etait masque. Verifie
    le 25/08/2026 :

        depuis un logger enfant : token=abcdef0123456789     <- en clair
        depuis la racine        : token=***

    Les HANDLERS, eux, voient les enregistrements propages. C'est donc la qu'il faut
    poser le filtre.

    Idempotent : `setup_logging` peut etre appele plusieurs fois, et des filtres qui
    s'accumulent n'ajoutent rien qu'un cout.
    """
    from colaig.security.secrets_filter import SecretsMaskingFilter

    racine = logging.getLogger()
    for handler in racine.handlers:
        if not any(isinstance(f, SecretsMaskingFilter) for f in handler.filters):
            handler.addFilter(SecretsMaskingFilter())


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Retourne un logger structuré nommé.

    Args:
        name: Nom du module (ex: "colaig.rag.retriever").

    Returns:
        Logger structuré prêt à l'emploi.
    """
    return structlog.get_logger(name)
