"""
Compatibilité — redirige vers webdav_instance.py qui est le point d'entrée unique.

Ce module existait en doublon de webdav_instance.py avec son propre cache
d'instances, ce qui provoquait des instances WebDAV non partagées.
Il est conservé uniquement pour ne pas casser les imports existants.
"""

from app.services.webdav_instance import get_webdav_service, ensure_webdav_initialized as with_webdav_service  # noqa: F401

__all__ = ['get_webdav_service', 'with_webdav_service']
