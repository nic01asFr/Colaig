import logging
import asyncio
import re
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import httpx
import urllib.parse
import unicodedata

logger = logging.getLogger(__name__)

class OCSLinkValidator:
    """
    Service pour valider et diagnostiquer les liens de partage OCS Nextcloud.
    
    Ce service permet de :
    - Vérifier la configuration de l'API OCS
    - Diagnostiquer les problèmes de génération des liens
    - Valider les liens de partage existants
    - Proposer des corrections automatiques
    """
    
    def __init__(self, base_url: str, username: str, password: str):
        """
        Initialise le validateur OCS.
        
        Args:
            base_url: URL de base du serveur Nextcloud
            username: Nom d'utilisateur pour l'authentification
            password: Mot de passe pour l'authentification
        """
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.http_client = httpx.AsyncClient(
            auth=(username, password),
            timeout=30.0,
            verify=False  # Pour les certificats auto-signés
        )
    
    async def validate_ocs_configuration(self) -> Dict[str, Any]:
        """
        Valide la configuration OCS du serveur Nextcloud.
        
        Returns:
            Dict avec les résultats de validation
        """
        results = {
            "is_valid": False,
            "capabilities": {},
            "sharing_enabled": False,
            "public_sharing_enabled": False,
            "api_version": None,
            "errors": []
        }
        
        try:
            # Vérifier les capacités du serveur
            logger.info("[OCS_VALIDATOR] Vérification des capacités du serveur")
            
            # Construire l'URL des capacités selon la spécification
            capabilities_url = f"{self.base_url}/ocs/v2.php/cloud/capabilities"
            
            headers = {
                "OCS-APIRequest": "true",
                "Accept": "application/json"
            }
            
            response = await self.http_client.get(
                capabilities_url, 
                headers=headers,
                params={"format": "json"}
            )
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    ocs_data = data.get("ocs", {})
                    
                    # Vérification du statut OCS (100 = succès selon la spécification)
                    meta_status = ocs_data.get("meta", {}).get("status")
                    meta_statuscode = ocs_data.get("meta", {}).get("statuscode")
                    
                    if meta_status == "ok" or meta_statuscode == 100:
                        capabilities = ocs_data.get("data", {}).get("capabilities", {})
                        results["capabilities"] = capabilities
                        
                        # Vérifier les capacités de partage
                        files_sharing = capabilities.get("files_sharing", {})
                        if files_sharing:
                            results["sharing_enabled"] = True
                            results["public_sharing_enabled"] = files_sharing.get("public", {}).get("enabled", False)
                            
                            # Vérifier la version de l'API
                            api_enabled = files_sharing.get("api_enabled", False)
                            if api_enabled:
                                results["api_version"] = "v1"
                                results["is_valid"] = True
                            else:
                                results["errors"].append("API de partage désactivée")
                        else:
                            results["errors"].append("Capacités de partage non disponibles")
                    else:
                        results["errors"].append(f"Erreur dans la réponse OCS: {meta_status}, code: {meta_statuscode}")
                        
                except Exception as json_error:
                    results["errors"].append(f"Erreur parsing JSON: {str(json_error)}")
            else:
                results["errors"].append(f"Erreur HTTP: {response.status_code}")
                
        except Exception as e:
            results["errors"].append(f"Erreur de connexion: {str(e)}")
            
        return results
    
    async def test_share_creation(self, test_path: str = "/test_share.txt") -> Dict[str, Any]:
        """
        Teste la création d'un partage pour diagnostiquer les problèmes.
        
        Args:
            test_path: Chemin du fichier de test
            
        Returns:
            Dict avec les résultats du test
        """
        results = {
            "public_share_success": False,
            "public_share_url": None,
            "user_share_success": False,
            "user_share_url": None,
            "errors": [],
            "raw_responses": []
        }
        
        try:
            # Test 1: Créer un lien public
            logger.info(f"[OCS_VALIDATOR] Test de création d'un lien public pour: {test_path}")
            
            public_result = await self._test_public_share(test_path)
            results["public_share_success"] = public_result["success"]
            results["public_share_url"] = public_result.get("url")
            results["raw_responses"].append(public_result)
            
            if not public_result["success"]:
                results["errors"].extend(public_result.get("errors", []))
            
            # Test 2: Créer un partage utilisateur (avec soi-même)
            logger.info(f"[OCS_VALIDATOR] Test de création d'un partage utilisateur pour: {test_path}")
            
            user_result = await self._test_user_share(test_path, self.username)
            results["user_share_success"] = user_result["success"]
            results["user_share_url"] = user_result.get("url")
            results["raw_responses"].append(user_result)
            
            if not user_result["success"]:
                results["errors"].extend(user_result.get("errors", []))
                
        except Exception as e:
            results["errors"].append(f"Erreur générale lors du test: {str(e)}")
            
        return results
    
    async def _test_public_share(self, path: str) -> Dict[str, Any]:
        """
        Teste la création d'un lien public.
        
        Args:
            path: Chemin du fichier à partager
            
        Returns:
            Dict avec les résultats du test
        """
        result = {
            "success": False,
            "url": None,
            "errors": [],
            "response_data": None
        }
        
        try:
            # Construire l'URL de l'API OCS
            ocs_url = f"{self.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
            
            # Traiter le chemin pour assurer la compatibilité
            # 1. Décoder d'abord si le chemin contient des caractères URL-encodés
            if "%" in path:
                path = urllib.parse.unquote(path)
                logger.info(f"[OCS_VALIDATOR] Chemin décodé: {path}")
            
            # 2. Normaliser le chemin pour une représentation cohérente des caractères Unicode
            normalized_path = unicodedata.normalize('NFC', path)
            if normalized_path != path:
                logger.info(f"[OCS_VALIDATOR] Normalisation Unicode: '{path}' -> '{normalized_path}'")
                path = normalized_path
            
            # Paramètres du partage public selon la spécification officielle
            params = {
                "path": path,
                "shareType": 3,  # 3 = lien public selon la documentation
                "permissions": 1,  # 1 = lecture seule selon la documentation
                "format": "json",
                "expireDate": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            }
            
            headers = {
                "OCS-APIRequest": "true",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            }
            
            logger.info(f"[OCS_VALIDATOR] Test de partage public pour: {path}")
            response = await self.http_client.post(ocs_url, data=params, headers=headers)
            
            logger.info(f"[OCS_VALIDATOR] Réponse lien public: {response.status_code}")
            
            if response.status_code in [200, 201]:
                try:
                    response_data = response.json()
                    result["response_data"] = response_data
                    
                    ocs_data = response_data.get("ocs", {})
                    meta = ocs_data.get("meta", {})
                    data = ocs_data.get("data", {})
                    
                    # Vérifier le statut OCS (100 = succès selon la documentation)
                    meta_status = meta.get("status")
                    meta_statuscode = meta.get("statuscode")
                    
                    if meta_status == "ok" or meta_statuscode == 100:
                        share_url = data.get("url")
                        share_token = data.get("token")
                        
                        # Vérifier si nous avons une URL directe
                        if share_url:
                            result["success"] = True
                            result["url"] = share_url
                            logger.info(f"[OCS_VALIDATOR] Lien public créé: {share_url}")
                        # Sinon, essayer de construire à partir du token
                        elif share_token:
                            share_url = f"{self.base_url}/s/{share_token}"
                            result["success"] = True
                            result["url"] = share_url
                            logger.info(f"[OCS_VALIDATOR] Lien public construit: {share_url}")
                        else:
                            result["errors"].append("URL et token manquants dans la réponse")
                    else:
                        error_msg = meta.get("message", "Erreur inconnue")
                        status_code = meta.get("statuscode")
                        result["errors"].append(f"Erreur OCS ({status_code}): {error_msg}")
                        
                except Exception as json_error:
                    result["errors"].append(f"Erreur parsing JSON: {str(json_error)}")
                    logger.error(f"[OCS_VALIDATOR] Réponse brute: {response.text}")
            else:
                result["errors"].append(f"Erreur HTTP: {response.status_code}")
                logger.error(f"[OCS_VALIDATOR] Réponse d'erreur: {response.text}")
                
        except Exception as e:
            result["errors"].append(f"Erreur requête: {str(e)}")
            
        return result
    
    async def _test_user_share(self, path: str, target_user: str) -> Dict[str, Any]:
        """
        Teste la création d'un partage utilisateur.
        
        Args:
            path: Chemin du fichier à partager
            target_user: Utilisateur cible
            
        Returns:
            Dict avec les résultats du test
        """
        result = {
            "success": False,
            "url": None,
            "errors": [],
            "response_data": None
        }
        
        try:
            # Construire l'URL de l'API OCS selon les spécifications
            ocs_url = f"{self.base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
            
            # Traiter le chemin pour assurer la compatibilité
            # 1. Décoder d'abord si le chemin contient des caractères URL-encodés
            if "%" in path:
                path = urllib.parse.unquote(path)
                logger.info(f"[OCS_VALIDATOR] Chemin décodé: {path}")
            
            # 2. Normaliser le chemin pour une représentation cohérente des caractères Unicode
            normalized_path = unicodedata.normalize('NFC', path)
            if normalized_path != path:
                logger.info(f"[OCS_VALIDATOR] Normalisation Unicode: '{path}' -> '{normalized_path}'")
                path = normalized_path
            
            # Extraire le nom d'utilisateur simple
            username = target_user.split('@')[0] if '@' in target_user else target_user
            
            # Paramètres du partage utilisateur selon les spécifications
            params = {
                "path": path,
                "shareType": 0,  # 0 = partage utilisateur selon la documentation
                "shareWith": username,
                "permissions": 1,  # 1 = lecture seule selon la documentation
                "format": "json",
                "expireDate": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            }
            
            headers = {
                "OCS-APIRequest": "true",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            }
            
            logger.info(f"[OCS_VALIDATOR] Test de partage utilisateur pour: {path} avec {username}")
            response = await self.http_client.post(ocs_url, data=params, headers=headers)
            
            logger.info(f"[OCS_VALIDATOR] Réponse partage utilisateur: {response.status_code}")
            
            if response.status_code in [200, 201]:
                try:
                    response_data = response.json()
                    result["response_data"] = response_data
                    
                    ocs_data = response_data.get("ocs", {})
                    meta = ocs_data.get("meta", {})
                    data = ocs_data.get("data", {})
                    
                    # Vérifier le statut OCS (100 = succès selon la documentation)
                    meta_status = meta.get("status")
                    meta_statuscode = meta.get("statuscode")
                    
                    if meta_status == "ok" or meta_statuscode == 100:
                        share_url = data.get("url")
                        if share_url:
                            result["success"] = True
                            result["url"] = share_url
                            logger.info(f"[OCS_VALIDATOR] Partage utilisateur créé: {share_url}")
                        else:
                            # Pour les partages utilisateur, il se peut qu'aucune URL ne soit retournée
                            # selon la configuration de Nextcloud
                            share_id = data.get("id")
                            if share_id:
                                result["success"] = True
                                result["share_id"] = share_id
                                logger.info(f"[OCS_VALIDATOR] Partage utilisateur créé avec ID: {share_id}")
                            else:
                                result["errors"].append("Ni URL ni ID dans la réponse")
                    else:
                        error_msg = meta.get("message", "Erreur inconnue")
                        status_code = meta.get("statuscode")
                        result["errors"].append(f"Erreur OCS ({status_code}): {error_msg}")
                        
                except Exception as json_error:
                    result["errors"].append(f"Erreur parsing JSON: {str(json_error)}")
                    logger.error(f"[OCS_VALIDATOR] Réponse brute: {response.text}")
            else:
                result["errors"].append(f"Erreur HTTP: {response.status_code}")
                logger.error(f"[OCS_VALIDATOR] Réponse d'erreur: {response.text}")
                
        except Exception as e:
            result["errors"].append(f"Erreur requête: {str(e)}")
            
        return result
    
    async def validate_existing_link(self, link: str) -> Dict[str, Any]:
        """
        Valide un lien existant pour déterminer s'il est au bon format.
        
        Args:
            link: Lien à valider
            
        Returns:
            Dict avec les résultats de validation
        """
        results = {
            "is_valid_format": False,
            "link_type": None,
            "is_webdav_fallback": False,
            "suggestions": []
        }
        
        try:
            # Déterminer le type de lien
            if "/remote.php/dav/files/" in link:
                results["is_webdav_fallback"] = True
                results["link_type"] = "webdav_fallback"
                results["suggestions"].append("Ce lien utilise un accès WebDAV direct au lieu d'un partage OCS")
                results["suggestions"].append("Vérifiez la configuration de l'API OCS")
                
            elif "/s/" in link:
                results["is_valid_format"] = True
                results["link_type"] = "ocs_public"
                results["suggestions"].append("Lien de partage public OCS valide")
                
            elif "/f/" in link:
                results["is_valid_format"] = True
                results["link_type"] = "ocs_direct"
                results["suggestions"].append("Lien de partage direct OCS valide")
                
            else:
                results["link_type"] = "unknown"
                results["suggestions"].append("Format de lien non reconnu")
                
        except Exception as e:
            results["suggestions"].append(f"Erreur lors de la validation: {str(e)}")
            
        return results
    
    async def generate_diagnostic_report(self, test_file_path: str = "/test_diagnostic.txt") -> str:
        """
        Génère un rapport de diagnostic complet.
        
        Args:
            test_file_path: Chemin du fichier de test
            
        Returns:
            Rapport de diagnostic formaté
        """
        report_lines = []
        report_lines.append("=== RAPPORT DE DIAGNOSTIC OCS ===")
        report_lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"Serveur: {self.base_url}")
        report_lines.append(f"Utilisateur: {self.username}")
        report_lines.append("")
        
        # Validation de la configuration
        report_lines.append("1. VALIDATION DE LA CONFIGURATION")
        config_results = await self.validate_ocs_configuration()
        
        if config_results["is_valid"]:
            report_lines.append("   ✅ Configuration OCS valide")
            report_lines.append(f"   ✅ Partage activé: {config_results['sharing_enabled']}")
            report_lines.append(f"   ✅ Partage public activé: {config_results['public_sharing_enabled']}")
        else:
            report_lines.append("   ❌ Configuration OCS invalide")
            for error in config_results["errors"]:
                report_lines.append(f"   ❌ {error}")
        
        report_lines.append("")
        
        # Test de création de partages
        report_lines.append("2. TESTS DE CRÉATION DE PARTAGES")
        share_results = await self.test_share_creation(test_file_path)
        
        if share_results["public_share_success"]:
            report_lines.append("   ✅ Création de lien public réussie")
            report_lines.append(f"   📎 URL: {share_results['public_share_url']}")
        else:
            report_lines.append("   ❌ Échec de création de lien public")
            for error in share_results["errors"]:
                report_lines.append(f"   ❌ {error}")
        
        if share_results["user_share_success"]:
            report_lines.append("   ✅ Création de partage utilisateur réussie")
            report_lines.append(f"   📎 URL: {share_results['user_share_url']}")
        else:
            report_lines.append("   ❌ Échec de création de partage utilisateur")
        
        report_lines.append("")
        
        # Recommandations
        report_lines.append("3. RECOMMANDATIONS")
        
        if not config_results["is_valid"]:
            report_lines.append("   🔧 Vérifiez la configuration du serveur Nextcloud")
            report_lines.append("   🔧 Assurez-vous que l'API OCS est activée")
            report_lines.append("   🔧 Vérifiez les permissions de partage")
        
        if not share_results["public_share_success"] and not share_results["user_share_success"]:
            report_lines.append("   🔧 Vérifiez les identifiants d'authentification")
            report_lines.append("   🔧 Vérifiez que le fichier de test existe")
            report_lines.append("   🔧 Vérifiez les permissions sur le serveur")
        
        if config_results["is_valid"] and (share_results["public_share_success"] or share_results["user_share_success"]):
            report_lines.append("   ✅ La configuration OCS fonctionne correctement")
            report_lines.append("   💡 Le problème peut venir de la gestion des chemins de fichiers")
            
            # Ajout des recommandations spécifiques aux problèmes de caractères spéciaux
            report_lines.append("")
            report_lines.append("4. CARACTÈRES SPÉCIAUX")
            report_lines.append("   💡 Pour les noms de fichiers avec caractères spéciaux:")
            report_lines.append("     - Utiliser la normalisation Unicode NFC")
            report_lines.append("     - Éviter les caractères interdits dans les noms de fichiers: < > : \" / \\ | ? *")
            report_lines.append("     - Ne pas utiliser d'espaces en début/fin de nom")
            report_lines.append("     - Encoder correctement les URL par segments de chemin")
        
        report_lines.append("")
        report_lines.append("=== FIN DU RAPPORT ===")
        
        return "\n".join(report_lines)
    
    async def close(self):
        """Ferme le client HTTP."""
        await self.http_client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close() 