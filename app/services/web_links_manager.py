"""
Service de gestion des liens web pour ColaIG.
Stocke et organise les liens web classés par catégories.
"""

import json
import os
from typing import Dict, List, Optional, Any
from datetime import datetime

from app.config import Config
from app.matrix_bot.config import logger

class WebLinksManager:
    """
    Gestionnaire de liens web pour ColaIG.
    Stocke et organise les liens web par catégories.
    """
    
    # Chemin relatif pour le fichier de liens
    WEB_LINKS_PATH = ".albert/web_links/links.json"
    
    def __init__(self, config: Config, webdav_service = None):
        self.config = config
        self.webdav_service = webdav_service
        self.links_by_category = {}
        self.loaded = False
        
    async def ensure_initialized(self):
        """Assure que le gestionnaire est initialisé avec les données à jour."""
        if not self.loaded:
            await self.load_links()
    
    async def load_links(self):
        """Charge les liens depuis WebDAV."""
        if not self.webdav_service:
            from app.services.webdav_instance import get_webdav_service
            self.webdav_service = await get_webdav_service(self.config)
        
        # Créer le dossier de liens s'il n'existe pas
        links_dir = os.path.dirname(os.path.join(self.config.webdav_root_path, self.WEB_LINKS_PATH))
        if not await self.webdav_service.exists(links_dir):
            await self.webdav_service.create_directory(links_dir)
        
        # Charger les liens s'ils existent
        links_path = os.path.join(self.config.webdav_root_path, self.WEB_LINKS_PATH)
        if await self.webdav_service.exists(links_path):
            try:
                content = await self.webdav_service.read_document(links_path)
                self.links_by_category = json.loads(content)
                logger.info(f"Liens web chargés: {len(self.links_by_category)} catégories")
            except Exception as e:
                logger.error(f"Erreur lors du chargement des liens: {str(e)}")
                self.links_by_category = {}
        else:
            # Initialiser avec quelques catégories par défaut
            self.links_by_category = {
                "administration": [],
                "general": [],
                "technique": [],
                "documentation": []
            }
            await self.save_links()
        
        self.loaded = True
            
    async def save_links(self):
        """Sauvegarde les liens sur WebDAV."""
        if not self.webdav_service:
            from app.services.webdav_instance import get_webdav_service
            self.webdav_service = await get_webdav_service(self.config)
        
        links_path = os.path.join(self.config.webdav_root_path, self.WEB_LINKS_PATH)
        content = json.dumps(self.links_by_category, ensure_ascii=False, indent=2)
        
        try:
            await self.webdav_service.write_file(links_path, content)
            logger.info("Liens web sauvegardés avec succès")
            return True
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde des liens: {str(e)}")
            return False
    
    async def add_link(self, url: str, title: str, category: str, description: str = ""):
        """Ajoute un lien à une catégorie."""
        await self.ensure_initialized()
        
        # Créer la catégorie si elle n'existe pas
        if category not in self.links_by_category:
            self.links_by_category[category] = []
        
        # Vérifier si le lien existe déjà dans cette catégorie
        for link in self.links_by_category[category]:
            if link["url"] == url:
                # Mettre à jour le lien existant
                link["title"] = title
                link["description"] = description
                link["updated_at"] = datetime.now().isoformat()
                await self.save_links()
                return True
        
        # Ajouter le nouveau lien
        self.links_by_category[category].append({
            "url": url,
            "title": title,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "visits": 0
        })
        
        await self.save_links()
        return True
    
    async def get_links_by_category(self, category: str = None) -> Dict[str, List[Dict]]:
        """Récupère les liens par catégorie."""
        await self.ensure_initialized()
        
        if category:
            if category in self.links_by_category:
                return {category: self.links_by_category[category]}
            return {}
        
        return self.links_by_category
    
    async def get_all_links(self) -> List[Dict]:
        """Récupère tous les liens, quelle que soit la catégorie."""
        await self.ensure_initialized()
        
        all_links = []
        for category, links in self.links_by_category.items():
            for link in links:
                link_with_category = link.copy()
                link_with_category["category"] = category
                all_links.append(link_with_category)
        
        return all_links
    
    async def delete_link(self, url: str) -> bool:
        """Supprime un lien par son URL."""
        await self.ensure_initialized()
        
        for category, links in self.links_by_category.items():
            for i, link in enumerate(links):
                if link["url"] == url:
                    links.pop(i)
                    await self.save_links()
                    return True
        
        return False
    
    async def increment_visit(self, url: str) -> bool:
        """Incrémente le compteur de visites d'un lien."""
        await self.ensure_initialized()
        
        for category, links in self.links_by_category.items():
            for link in links:
                if link["url"] == url:
                    link["visits"] = link.get("visits", 0) + 1
                    link["last_visited"] = datetime.now().isoformat()
                    await self.save_links()
                    return True
        
        return False

# Instance unique pour tout le programme
_web_links_manager = None

async def get_web_links_manager(config: Config) -> WebLinksManager:
    """Récupère l'instance unique du gestionnaire de liens web."""
    global _web_links_manager
    
    if _web_links_manager is None:
        from app.services.webdav_instance import get_webdav_service
        webdav_service = await get_webdav_service(config)
        _web_links_manager = WebLinksManager(config, webdav_service)
        await _web_links_manager.ensure_initialized()
    
    return _web_links_manager 