"""
Service pour gérer l'instance unique du service WebDAV.
Évite la duplication des connexions WebDAV dans l'application.
"""

from app.config import Config
from app.matrix_bot.config import logger
from typing import Optional, Callable, Any
from functools import wraps
import asyncio
import inspect

from app.services.webdav import WebDAVService
from app.services.webdav_context_manager import get_webdav_context_manager

# Instance unique du service WebDAV par défaut (pour compatibilité)
_webdav_service = None
_webdav_lock = asyncio.Lock()

# Décorateur pour garantir l'initialisation du service WebDAV
def ensure_webdav_initialized(func: Callable) -> Callable:
    """
    Décorateur qui garantit que le service WebDAV est initialisé avant d'exécuter la fonction.
    
    Args:
        func: Fonction à décorer
        
    Returns:
        Fonction décorée
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Récupérer la configuration
        config = None
        for arg in args:
            if isinstance(arg, Config):
                config = arg
                break
        
        if not config and 'config' in kwargs:
            config = kwargs['config']
            
        if not config:
            # Chercher dans les attributs des objets passés
            for arg in args:
                if hasattr(arg, 'config'):
                    config = arg.config
                    break
                    
        if not config:
            # Fallback à la configuration par défaut
            from app.config import env_config
            config = env_config
            
        # Récupérer room_id et user_id s'ils sont présents
        room_id = kwargs.get('room_id')
        user_id = kwargs.get('user_id')
        
        # Initialiser le service WebDAV
        webdav_service = await get_webdav_service(config, room_id, user_id)
        
        # Ajouter le service WebDAV aux arguments
        if 'webdav_service' in inspect.signature(func).parameters:
            kwargs['webdav_service'] = webdav_service
            
        # Exécuter la fonction
        return await func(*args, **kwargs)
    
    return wrapper

async def get_webdav_service(config: Config, room_id: Optional[str] = None, user_id: Optional[str] = None):
    """
    Récupère l'instance du service WebDAV appropriée pour le contexte donné.
    
    Si room_id et/ou user_id sont fournis, utilise le gestionnaire de contextes WebDAV
    pour récupérer le service approprié au contexte.
    
    Args:
        config: Configuration générale
        room_id: ID du salon Matrix (optionnel)
        user_id: ID de l'utilisateur (optionnel)
        
    Returns:
        Une instance du service WebDAV
    """
    global _webdav_service
    
    try:
        # Si on a fourni un contexte, utiliser le gestionnaire contextuel
        if room_id or user_id:
            try:
                # Récupérer le gestionnaire contextuel
                ctx_manager = await get_webdav_context_manager(config)
                # Obtenir le service WebDAV pour ce contexte
                webdav = await ctx_manager.get_webdav_for_context(room_id, user_id)
                if webdav:
                    return webdav
                else:
                    logger.error("Impossible d'obtenir un service WebDAV contextuel, retour à l'instance par défaut")
            except Exception as e:
                logger.error(f"Erreur lors de l'obtention du WebDAV contextuel: {str(e)}")
        
        # Aucun contexte spécifié ou erreur : utiliser l'instance globale
        async with _webdav_lock:
            if _webdav_service is None:
                # Vérifier que la configuration est valide
                logger.info(f"[WEBDAV_INIT] Vérification de la configuration WebDAV")
                logger.info(f"[WEBDAV_INIT] URL: {config.webdav_url}, Username: {'présent' if config.webdav_username else 'manquant'}, Password: {'présent' if config.webdav_password else 'manquant'}")
                
                if not (config.webdav_url and config.webdav_username and config.webdav_password):
                    logger.error("[WEBDAV_INIT] Configuration WebDAV incomplète, impossible d'initialiser le service")
                    # Retourner un service WebDAV en mode dégradé qui loggera les erreurs sans planter
                    return FallbackWebDAVService()
                
                try:
                    # Créer une nouvelle instance
                    from app.services.webdav import WebDAVService
                    logger.info(f"[WEBDAV_INIT] Initialisation du service WebDAV avec URL={config.webdav_url}, Username={config.webdav_username}, Root={config.webdav_root_path}")
                    _webdav_service = WebDAVService(
                        config=config,
                        webdav_url=config.webdav_url,
                        webdav_username=config.webdav_username,
                        webdav_password=config.webdav_password,
                        webdav_root=config.webdav_root_path
                    )
                    await _webdav_service.init_webdav()
                    logger.info("[WEBDAV_INIT] Service WebDAV initialisé avec succès")
                except Exception as init_error:
                    logger.error(f"[WEBDAV_INIT] Erreur lors de l'initialisation du service WebDAV: {str(init_error)}")
                    import traceback
                    logger.error(f"[WEBDAV_INIT] Traceback détaillé: {traceback.format_exc()}")
                    # Retourner un service WebDAV en mode dégradé
                    return FallbackWebDAVService()
                
            return _webdav_service
            
    except Exception as e:
        logger.error(f"[WEBDAV_INIT] Erreur grave lors de l'initialisation du service WebDAV: {str(e)}")
        import traceback
        logger.error(f"[WEBDAV_INIT] Traceback détaillé: {traceback.format_exc()}")
        return FallbackWebDAVService()

# Un service de secours qui ne plante pas mais ne fait rien
class FallbackWebDAVService:
    """
    Service WebDAV de secours qui ne fait rien mais ne plante pas.
    Utilisé en cas d'erreur grave d'initialisation du vrai service.
    """
    def __init__(self):
        self.webdav_url = "fallback://none"
        self.webdav_username = "fallback"
        self.webdav_password = "fallback"
        self.webdav_root = "/"
        self.CONTEXTS_DIR = ".albert/contexts"
        # Créer un objet simple avec l'attribut webdav_url au lieu d'utiliser AsyncClientConfig
        class SimpleConfig:
            def __init__(self):
                self.webdav_url = "fallback://none"
                # Ajouter tous les attributs nécessaires pour éviter les erreurs
                self.webdav_username = "fallback"
                self.webdav_password = "fallback"
                self.webdav_root_path = "/"
        self.config = SimpleConfig()
        self.base_url = "fallback://none"  # Ajout de base_url pour la méthode create_share_link
        
        # Tenter de charger le gestionnaire de contexte
        try:
            import importlib
            # Charger dynamiquement le module pour éviter les dépendances circulaires
            context_module = importlib.import_module('app.services.context.manager')
            # Vérifier si l'instance est déjà initialisée
            self.context_manager = getattr(context_module, 'context_manager_instance', None)
            if self.context_manager:
                logger.info("[WEBDAV_FALLBACK] Gestionnaire de contexte trouvé et accessible")
            else:
                logger.warning("[WEBDAV_FALLBACK] Gestionnaire de contexte non initialisé")
        except Exception as e:
            logger.warning(f"[WEBDAV_FALLBACK] Impossible d'accéder au gestionnaire de contexte: {str(e)}")
            self.context_manager = None
        
    async def init_webdav(self):
        logger.warning("Initialisation du service WebDAV de secours (mode dégradé)")
        return self
        
    async def exists(self, *args, **kwargs):
        logger.warning("WebDAV en mode dégradé: méthode exists appelée")
        return False
        
    async def read_document(self, *args, **kwargs):
        logger.warning("WebDAV en mode dégradé: méthode read_document appelée")
        return "{}"
        
    async def write_document(self, *args, **kwargs):
        logger.warning("WebDAV en mode dégradé: méthode write_document appelée")
        return True
        
    async def list_documents(self, *args, **kwargs):
        logger.warning("WebDAV en mode dégradé: méthode list_documents appelée")
        return []
        
    async def close(self):
        logger.warning("WebDAV en mode dégradé: méthode close appelée")
        return True
        
    def _get_url(self, path: str) -> str:
        """
        Version de secours de _get_url qui retourne une URL fallback.
        
        Args:
            path: Chemin du fichier
            
        Returns:
            URL WebDAV de fallback
        """
        logger.warning(f"WebDAV en mode dégradé: méthode _get_url appelée pour {path}")
        return f"fallback://none/{path.lstrip('/')}"
        
    async def create_share_link(self, path: str, password=None, expiration_days=None, target_user=None):
        """
        Version améliorée de create_share_link qui génère systématiquement un lien public.
        
        Args:
            path: Chemin du fichier
            password: Mot de passe optionnel pour le partage
            expiration_days: Nombre de jours avant expiration
            target_user: Ignoré, mais conservé pour compatibilité
            
        Returns:
            URL du lien de partage ou WebDAV directe en dernier recours
        """
        logger.warning("[OCS_SHARE] Service en mode dégradé: tentative d'utilisation de l'API OCS")
        
        # Récupérer les configurations depuis les variables d'environnement
        from app.config import env_config
        base_url = env_config.webdav_url if hasattr(env_config, 'webdav_url') else "https://bnum.din.gouv.fr/mdrive"
        webdav_username = env_config.webdav_username if hasattr(env_config, 'webdav_username') else ""
        webdav_password = env_config.webdav_password if hasattr(env_config, 'webdav_password') else ""
        webdav_root_path = env_config.webdav_root_path if hasattr(env_config, 'webdav_root_path') else "/documents"
        
        # Nettoyer et normaliser le chemin racine
        webdav_root_path = webdav_root_path.strip('/')
        if webdav_root_path and not webdav_root_path.endswith('/'):
            webdav_root_path += '/'
        
        # Nettoyer l'URL de base
        if "/remote.php/dav/files/" in base_url:
            base_url = base_url.split("/remote.php/dav/files/")[0]
        elif "/remote.php/webdav/" in base_url:
            base_url = base_url.split("/remote.php/webdav/")[0]
        base_url = base_url.rstrip('/')
        
        logger.info(f"[OCS_SHARE] FallbackWebDAVService: URL base={base_url}, username={webdav_username}, root_path={webdav_root_path}")
        
        # Si les informations de base sont manquantes, revenir au comportement par défaut
        if not base_url or not webdav_username or not webdav_password:
            logger.error("[OCS_SHARE] FallbackWebDAVService: configuration incomplète, fallback vers URL WebDAV directe")
            return self._create_fallback_webdav_url(base_url, path, target_user, webdav_root_path)
            
        try:
            # Si nous avons un chemin, essayer d'utiliser l'API OCS pour un lien public
            if path:
                import httpx
                import urllib.parse
                import unicodedata
                from datetime import datetime, timedelta
                
                # Récupérer le nom de l'espace de travail (sans faire d'appel asynchrone)
                workspace_name = "dossiers-colaig-assistant"  # Valeur par défaut
                
                # Tenter de récupérer l'espace de travail depuis le contexte actuel
                try:
                    if self.context_manager and target_user:
                        # Vérifier si nous avons un contexte pour cet utilisateur
                        user_contexts = getattr(self.context_manager, '_user_contexts', {})
                        if target_user in user_contexts:
                            user_context = user_contexts.get(target_user)
                            if user_context and hasattr(user_context, 'webdav_workspace'):
                                workspace_name = user_context.webdav_workspace
                                logger.info(f"[OCS_SHARE] Espace de travail trouvé pour {target_user}: {workspace_name}")
                except Exception as e:
                    logger.warning(f"[OCS_SHARE] Erreur lors de la récupération de l'espace de travail: {str(e)}")
                
                # Nettoyer le chemin pour l'API OCS (SANS slash initial)
                normalized_path = path.strip().lstrip('/')
                
                # Décodage URL si nécessaire
                if "%" in normalized_path:
                    normalized_path = urllib.parse.unquote(normalized_path)
                
                # Normalisation Unicode
                normalized_path = unicodedata.normalize('NFC', normalized_path)
                
                # Construire le chemin complet avec l'espace de travail
                # Format: espace_de_travail/chemin_du_document
                if not normalized_path.startswith(workspace_name):
                    if "/" in normalized_path:
                        # Si le chemin contient déjà une structure de dossiers
                        normalized_path = f"{workspace_name}/{normalized_path}"
                    else:
                        # Si c'est juste un nom de fichier
                        if webdav_root_path:
                            normalized_path = f"{workspace_name}/{webdav_root_path}/{normalized_path}"
                        else:
                            normalized_path = f"{workspace_name}/{normalized_path}"
                
                logger.info(f"[OCS_SHARE] FallbackWebDAVService: Chemin normalisé pour OCS: {normalized_path}")
                
                # URL de l'API OCS
                ocs_url = f"{base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
                
                # Paramètres pour le lien de partage public
                params = {
                    "path": normalized_path,
                    "shareType": 3,  # 3 = lien public
                    "permissions": 1,  # 1 = lecture seule
                    "format": "json"
                }
                
                # Ajouter une date d'expiration
                exp_days = expiration_days if expiration_days else 2
                expiry_date = (datetime.now() + timedelta(days=exp_days)).strftime("%Y-%m-%d")
                params["expireDate"] = expiry_date
                
                # Ajouter un mot de passe si fourni
                if password:
                    params["password"] = password
                    logger.info("[OCS_SHARE] FallbackWebDAVService: Lien public protégé par mot de passe")
                
                # En-têtes HTTP
                headers = {
                    "OCS-APIRequest": "true",  # Crucial pour l'API OCS
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json"
                }
                
                logger.info(f"[OCS_SHARE] FallbackWebDAVService: Tentative d'appel API OCS: {ocs_url}")
                logger.info(f"[OCS_SHARE] FallbackWebDAVService: Paramètres: {params}")
                logger.info(f"[OCS_SHARE] FallbackWebDAVService: Chemin final OCS: '{normalized_path}'")
                
                # Créer un client HTTP avec authentification
                async with httpx.AsyncClient(timeout=30.0, auth=(webdav_username, webdav_password)) as client:
                    # Appeler l'API OCS
                    response = await client.post(ocs_url, data=params, headers=headers)
                    
                    logger.info(f"[OCS_SHARE] FallbackWebDAVService: Réponse API: status={response.status_code}")
                    logger.info(f"[OCS_SHARE] FallbackWebDAVService: Contenu réponse: {response.text[:500]}")
                    
                    # Si la requête est réussie, traiter la réponse
                    if response.status_code in [200, 201]:
                        try:
                            response_data = response.json()
                            
                            # Vérifier la structure de la réponse OCS
                            ocs_data = response_data.get("ocs", {})
                            meta = ocs_data.get("meta", {})
                            data = ocs_data.get("data", {})
                            
                            # Vérifier le statut OCS
                            ocs_status = meta.get("status")
                            ocs_statuscode = meta.get("statuscode")
                            
                            # Si le partage est créé avec succès
                            if (ocs_status == "ok" and ocs_statuscode == 200) or ocs_statuscode == 100:
                                # Extraire l'URL du partage
                                share_url = data.get("url")
                                
                                # Si pas d'URL directe, essayer avec le token
                                if not share_url:
                                    token = data.get("token")
                                    if token:
                                        share_url = f"{base_url}/s/{token}"
                                        logger.info(f"[OCS_SHARE] FallbackWebDAVService: URL construite à partir du token: {share_url}")
                                
                                # Si nous avons une URL, la retourner
                                if share_url:
                                    logger.info(f"[OCS_SHARE] ✅ FallbackWebDAVService: Lien OCS créé avec succès: {share_url}")
                                    return share_url
                                else:
                                    logger.warning("[OCS_SHARE] FallbackWebDAVService: Pas d'URL dans la réponse")
                            else:
                                error_message = meta.get("message", "Erreur inconnue")
                                logger.warning(f"[OCS_SHARE] FallbackWebDAVService: Erreur API OCS: {error_message}")
                        except Exception as json_error:
                            logger.warning(f"[OCS_SHARE] FallbackWebDAVService: Erreur parsing JSON: {str(json_error)}")
                
                    # Si l'API OCS échoue, continuer vers le fallback
                    logger.warning("[OCS_SHARE] FallbackWebDAVService: Échec de l'API OCS, utilisation fallback WebDAV")
            else:
                logger.warning("[OCS_SHARE] FallbackWebDAVService: Chemin manquant")
                
        except Exception as e:
            logger.error(f"[OCS_SHARE] FallbackWebDAVService: Erreur lors de l'appel API OCS: {str(e)}")
            import traceback
            logger.error(f"[OCS_SHARE] FallbackWebDAVService: Traceback: {traceback.format_exc()}")
        
        # En cas d'échec, revenir à l'URL WebDAV directe
        return self._create_fallback_webdav_url(base_url, path, target_user, webdav_root_path)
        
    def _create_fallback_webdav_url(self, base_url: str, path: str, target_user=None, webdav_root_path=None) -> str:
        """
        Crée une URL WebDAV directe comme fallback ultime.
        
        Args:
            base_url: URL de base du serveur
            path: Chemin du fichier
            target_user: Utilisateur cible pour le lien
            webdav_root_path: Chemin racine WebDAV configuré
            
        Returns:
            URL WebDAV directe ou chaîne vide
        """
        logger.warning("[OCS_SHARE] FallbackWebDAVService: création d'un lien direct WebDAV")
        
        try:
            # Nettoyer le chemin
            normalized_path = path.strip().lstrip('/')
            
            # Détecter si le chemin contient un dossier parent
            parent_dir = None
            if "/" in normalized_path:
                parent_dir = normalized_path.split("/")[0]
                logger.info(f"[OCS_SHARE] Dossier parent détecté: {parent_dir}")
            
            # Déterminer l'utilisateur pour le lien WebDAV
            fallback_username = "colaig.assistant"
            if target_user:
                fallback_username = target_user
            
            # Extraction améliorée du prénom.nom
            if '@' in fallback_username:
                # Format Matrix
                if ':' in fallback_username:
                    username_part = fallback_username.split(':')[0]
                    if username_part.startswith('@'):
                        fallback_username = username_part[1:]
                # Format email
                else:
                    fallback_username = fallback_username.split('@')[0]
            
            # Gestion des formats spéciaux (prénom.nom-domaine.gouv.fr1)
            if '.' in fallback_username:
                # Cas 1: Format prénom.nom-domaine.gouv.fr1
                if '-' in fallback_username:
                    dot_pos = fallback_username.find('.')
                    dash_pos = fallback_username.find('-', dot_pos)
                    if dash_pos > dot_pos:
                        fallback_username = fallback_username[:dash_pos]
                        logger.info(f"[OCS_SHARE] Extraction du nom d'utilisateur: {target_user} -> {fallback_username}")
                
                # Cas 2: Format prénom.nom@domaine.gouv.fr
                elif '@' in fallback_username:
                    fallback_username = fallback_username.split('@')[0]
                    logger.info(f"[OCS_SHARE] Extraction du nom d'utilisateur depuis email: {target_user} -> {fallback_username}")
            
            # Utiliser l'URL WebDAV de la configuration si disponible
            from app.config import env_config
            if not base_url:
                base_url = env_config.webdav_url if hasattr(env_config, 'webdav_url') else "https://bnum.din.gouv.fr/mdrive"
            
            # Nettoyer l'URL de base
            if "/remote.php/dav/files/" in base_url:
                base_url = base_url.split("/remote.php/dav/files/")[0]
            elif "/remote.php/webdav/" in base_url:
                base_url = base_url.split("/remote.php/webdav/")[0]
            base_url = base_url.rstrip('/')
            
            # Récupérer l'espace de travail depuis le contexte WebDAV sans appel asynchrone
            workspace_path = "dossiers-colaig-assistant"  # Valeur par défaut
            
            # Tenter de récupérer l'espace de travail depuis le contexte actuel
            try:
                if self.context_manager and target_user:
                    # Vérifier si nous avons un contexte pour cet utilisateur
                    user_contexts = getattr(self.context_manager, '_user_contexts', {})
                    if target_user in user_contexts:
                        user_context = user_contexts.get(target_user)
                        if user_context and hasattr(user_context, 'webdav_workspace'):
                            workspace_path = user_context.webdav_workspace
                            logger.info(f"[OCS_SHARE] Espace de travail trouvé pour {target_user}: {workspace_path}")
            except Exception as e:
                logger.warning(f"[OCS_SHARE] Erreur lors de la récupération de l'espace de travail: {str(e)}")
            
            # Construire l'URL WebDAV de fallback avec le chemin complet et l'espace de travail
            # Format: espace_de_travail/chemin_du_document
            if not normalized_path.startswith(workspace_path):
                if "/" in normalized_path:
                    # Si le chemin contient déjà une structure de dossiers
                    normalized_path = f"{workspace_path}/{normalized_path}"
                else:
                    # Si c'est juste un nom de fichier
                    if webdav_root_path:
                        normalized_path = f"{workspace_path}/{webdav_root_path}/{normalized_path}"
                    else:
                        normalized_path = f"{workspace_path}/{normalized_path}"
            
            fallback_url = f"{base_url}/remote.php/dav/files/{fallback_username}/{normalized_path}?download=1"
            logger.info(f"[OCS_SHARE] ⚠️ URL WebDAV fallback construite: {fallback_url}")
            return fallback_url
            
        except Exception as e:
            logger.error(f"[OCS_SHARE] Erreur lors de la création de l'URL WebDAV: {str(e)}")
            return ""

async def close_webdav_service():
    """Ferme l'instance unique du service WebDAV."""
    global _webdav_service
    
    if _webdav_service:
        try:
            await _webdav_service.close()
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture du service WebDAV: {str(e)}")
        finally:
            _webdav_service = None

# Exporter les fonctions 
__all__ = ['get_webdav_service', 'close_webdav_service']