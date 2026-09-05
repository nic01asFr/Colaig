"""
agents/tools/storage_tools.py — Outils d'accès au stockage pour l'Orchestrateur.

Outils :
- fetch_document : télécharge et lit un document spécifique
- list_documents  : liste les fichiers d'un répertoire du workspace
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from colaig.models import ToolDefinition, ToolParameter, WorkspaceConfig

# Un titre markdown, quel que soit son niveau. Les documents du corpus portent leurs
# articles en « ## Article R2111-8 » ; d'autres corpus titrent autrement, et le
# decoupage par titre vaut pour tous.
_TITRE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


def _extraire_la_section(contenu: str, demandee: str) -> tuple[str | None, list[str]]:
    """Rend (la section demandee, la liste des titres du document).

    Le rapprochement est tolerant sur la forme : le modele ecrit « R2111-8 » aussi
    souvent que « Article R2111-8 ». Il reste EXACT sur le fond — on ne rapproche que
    par egalite ou par inclusion du titre demande dans le titre reel, jamais par
    ressemblance approximative, qui rendrait l'article voisin sans le dire.

    La section s'arrete au titre suivant de MEME NIVEAU OU PLUS HAUT : servir la fin du
    document depuis un titre reviendrait a ne rien cibler.
    """
    titres = [(m.start(), len(m.group(1)), m.group(2).strip()) for m in _TITRE.finditer(contenu)]
    if not titres:
        return None, []

    voulu = demandee.strip().casefold()
    trouve = None
    for i, (debut, niveau, titre) in enumerate(titres):
        t = titre.casefold()
        if t == voulu or voulu in t:
            trouve = (i, debut, niveau)
            break
    if trouve is None:
        return None, [t for _, _, t in titres]

    i, debut, niveau = trouve
    fin = len(contenu)
    for suivant_debut, suivant_niveau, _ in titres[i + 1:]:
        if suivant_niveau <= niveau:
            fin = suivant_debut
            break
    return contenu[debut:fin].rstrip(), [t for _, _, t in titres]

# ---------------------------------------------------------------------------
# Définitions

FETCH_DOCUMENT_DEFINITION = ToolDefinition(
    name="fetch_document",
    description=(
        "Télécharge et lit le contenu d'un document spécifique depuis le workspace. "
        "Utiliser quand la recherche sémantique identifie un document précis à lire "
        "en entier ou quand l'utilisateur mentionne un fichier par son nom."
    ),
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description=(
                "Chemin du document dans le storage (relatif à la racine du workspace). "
                "Exemple : 'documents/guide-procedures.pdf'"
            ),
            required=True,
        ),
        ToolParameter(
            name="max_chars",
            type="integer",
            description="Nombre maximum de caractères à retourner (défaut : 3000).",
            required=False,
        ),
        ToolParameter(
            name="section",
            type="string",
            description=(
                "Titre de la section à lire, si l'on cherche un passage précis plutôt "
                "que le début du document. Sans ce paramètre, seuls les premiers "
                "caractères sont rendus, et un passage situé plus loin reste "
                "inaccessible. Exemple : 'Article R2111-8'. Si le titre n'existe pas, "
                "la réponse liste les sections du document."
            ),
            required=False,
        ),
    ],
    category="storage",
)

LIST_DOCUMENTS_DEFINITION = ToolDefinition(
    name="list_documents",
    description=(
        "Liste les fichiers disponibles dans un répertoire du workspace. "
        "Utile pour explorer la structure des documents avant de les récupérer."
    ),
    parameters=[
        ToolParameter(
            name="directory",
            type="string",
            description=(
                "Répertoire à lister (relatif à la racine du workspace). "
                "Laisser vide ou '/' pour la racine."
            ),
            required=False,
        ),
    ],
    category="storage",
)


# ---------------------------------------------------------------------------
# Factories

def create_fetch_handler(storage, workspace: WorkspaceConfig | None = None) -> Callable:
    """Crée un handler async pour fetch_document.

    Args:
        storage: Implémentation de StorageProtocol.
        workspace: Configuration du workspace (pour résoudre le chemin racine).
    """
    workspace_root = workspace.storage_path if workspace else ""

    async def fetch_handler(path: str, max_chars: int | None = 3000,
                            section: str | None = None) -> str:
        """Télécharge et retourne le contenu d'un document, ou l'une de ses sections.

        POURQUOI `section` (05/09/2026)
        ---------------------------------
        Cet outil ne rendait que le DÉBUT du fichier, tronqué à 3000 caractères. Sur le
        corpus mesuré, 98 documents sur 108 dépassent ce seuil — médiane 9 061
        caractères, maximum 75 356. Un article situé au milieu d'un document restait
        donc hors d'atteinte, quelle que soit la valeur de `max_chars`, puisque c'est
        toujours la tête qu'on rendait.

        Le modèle le savait — la réponse porte `truncated` — et insistait : sur 202
        appels d'une campagne, 74 demandaient 5 000 caractères et 13 en demandaient
        10 000. Cela ne l'avançait guère.

        Le cas mp-057 s'expliquait entièrement ainsi : le modèle répondait « le document
        relatif à la définition du besoin a été identifié dans le sommaire », le
        demandait, recevait son en-tête, et concluait que l'information n'y figurait
        pas. Elle y figurait, quelques milliers de caractères plus loin.

        Returns:
            JSON string : {"path": ..., "content": ..., "size": ..., "truncated": ...}
            ou {"error": ..., "sections": [...]} si la section demandée n'existe pas —
            dire ce que le document porte évite un second appel à l'aveugle.
        """
        # Résolution du chemin absolu
        if workspace_root and not path.startswith("/"):
            full_path = f"{workspace_root.rstrip('/')}/{path.lstrip('/')}"
        else:
            full_path = path

        try:
            exists = await storage.exists(full_path)
            if not exists:
                return json.dumps({"error": f"Fichier introuvable : {path}"}, ensure_ascii=False)

            content_bytes = await storage.download(full_path)
            # Décodage UTF-8 avec fallback latin-1 pour les PDF/DOCX bruts
            try:
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                content = content_bytes.decode("latin-1", errors="replace")

            max_c = max_chars if max_chars is not None else 3000

            if section:
                extrait, titres = _extraire_la_section(content, section)
                if extrait is None:
                    return json.dumps({
                        "error": f"Section introuvable dans {path} : {section}",
                        "sections": titres[:80],
                    }, ensure_ascii=False)
                return json.dumps({
                    "path": path,
                    "section": section,
                    "content": extrait[:max_c],
                    "size": len(content_bytes),
                    "truncated": len(extrait) > max_c,
                }, ensure_ascii=False)

            truncated = len(content) > max_c
            return json.dumps({
                "path": path,
                "content": content[:max_c],
                "size": len(content_bytes),
                "truncated": truncated,
            }, ensure_ascii=False)

        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return fetch_handler


def create_list_handler(storage, workspace: WorkspaceConfig | None = None) -> Callable:
    """Crée un handler async pour list_documents.

    Args:
        storage: Implémentation de StorageProtocol.
        workspace: Configuration du workspace (pour résoudre le chemin racine).
    """
    workspace_root = workspace.storage_path if workspace else ""

    async def list_handler(directory: str | None = "") -> str:
        """Liste les fichiers d'un répertoire.

        Returns:
            JSON string : {"directory": ..., "files": [{"name": ..., "size": ..., "type": ...}]}
        """
        dir_path = directory or ""

        # Résolution du chemin absolu
        if workspace_root and not dir_path.startswith("/"):
            full_path = f"{workspace_root.rstrip('/')}/{dir_path.lstrip('/')}" if dir_path else workspace_root
        else:
            full_path = dir_path or "/"

        try:
            files = await storage.list_files(full_path)
            serialized = [
                {
                    "name": f.name,
                    "path": f.path,
                    "size": f.size,
                    "is_directory": f.is_directory,
                    "type": f.content_type or ("directory" if f.is_directory else "file"),
                }
                for f in files
            ]
            return json.dumps({
                "directory": dir_path or "/",
                "files": serialized,
                "count": len(serialized),
            }, ensure_ascii=False)

        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return list_handler
