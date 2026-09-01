"""
Colaig — Découpage de documents en chunks

Implémente ChunkerProtocol.
Stratégies adaptées par type de document :
- Markdown : split par sections (titres #)
- PDF/DOCX : split par paragraphes avec overlap
- Texte brut : sliding window
"""

from __future__ import annotations

import hashlib
import logging
import re

from colaig.models import DocumentChunk

logger = logging.getLogger(__name__)

# Bornes de taille
MIN_CHUNK_SIZE = 50
MAX_CHUNK_SIZE = 2000


# Un titre markdown quelconque, et un titre d'article.
_TITRE = re.compile(r"^#{1,6}\s+", re.M)
_TITRE_ARTICLE = re.compile(r"^##\s+Article\s+(.+)$", re.M)

# Le seuil est un CHOIX, pas un resultat. Mesure le 01/09/2026 sur les deux corpus
# reels : la part des titres qui sont des articles vaut 0,89 sur les marches publics
# (107 documents sur 108) et 0,00 sur le corpus SST (0 sur 60). Toute valeur
# intermediaire fonctionne, donc aucune n'est justifiee par la mesure.
#
# On le prend HAUT, parce que les deux erreurs ne coutent pas la meme chose : un faux
# positif casse le decoupage d'un document qui n'a rien demande, un faux negatif rend
# seulement le comportement d'avant.
_PART_MINIMALE = 0.5
_ARTICLES_MINIMUM = 2


def decoupage_par_article_pertinent(contenu: str) -> bool:
    """Ce document est-il structure en articles ?

    POURQUOI DETECTER PLUTOT QUE DECLARER. D12 (23/08/2026) avait tranche en faveur du
    decoupage par article pour un corpus structure — 82 % de recuperation complete
    contre 72 %, index 17 % plus petit — et en faisait un PARAMETRE D'ESPACE. C'etait
    demander a l'utilisateur de savoir ce qu'est un chunk ; il ne le fera pas, pas plus
    qu'il ne prepare son corpus.

    Le signal, lui, se lit tout seul : 99 % des documents d'un corpus contre 0 % de
    l'autre. Il n'y a pas de zone grise a arbitrer.

    POURQUOI PAR DOCUMENT ET NON PAR ESPACE. Un espace peut contenir cinquante PDF
    scannes et dix fichiers structures. Decider par espace obligerait a trancher pour
    tout le monde ; decider par document donne a chacun le decoupage que sa forme
    permet, et le cas mixte devient gratuit.

    LE MARQUEUR EST ETROIT — `## Article `, la forme du corpus mesure. Generaliser a
    partir d'un seul corpus serait du sur-mesure deguise en regle ; on relachera quand
    un espace reel le demandera, avec sa forme sous les yeux.
    """
    if not contenu:
        return False
    articles = len(_TITRE_ARTICLE.findall(contenu))
    if articles < _ARTICLES_MINIMUM:
        return False
    titres = len(_TITRE.findall(contenu))
    return bool(titres) and articles / titres >= _PART_MINIMALE


class Chunker:
    """Service de découpage de documents en chunks.

    Args:
        chunk_size: Taille cible des chunks en caractères.
        chunk_overlap: Chevauchement entre chunks en caractères.
    """

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 200,
                 strategie: str = "fenetre") -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        # « auto » : detection par document. « article » / « fenetre » : force.
        #
        # POURQUOI « fenetre » RESTE LE DEFAUT, ALORS QUE « auto » EST MEILLEUR.
        #
        # Mesure du 31/08/2026 sur le corpus des marches publics : la fenetre produit
        # deja 94 % de passages portant une identite d'article, « auto » en produit
        # 98 %. Quatre points — reels, mais qui ne justifient pas de deplacer l'index
        # ET la reference au milieu d'une campagne de mesure : toutes les comparaisons
        # en cours deviendraient incomparables, et l'on ne saurait plus attribuer les
        # ecarts.
        #
        # Le basculement du defaut est donc DIFFERE, pas abandonne : a decider une fois
        # la campagne du pipeline close. Sans cette echeance ecrite, « auto » serait une
        # capacite de plus qui existe et ne sert jamais.
        self._strategie = strategie

    def chunk_document(
        self,
        content: str,
        source_path: str,
        doc_type: str = "text",
        metadata: dict | None = None,
    ) -> list[DocumentChunk]:
        """Découpe un document en chunks.

        Args:
            content: Texte du document.
            source_path: Chemin WebDAV du document source.
            doc_type: Type de document (md, pdf, docx, txt, odt, html).
            metadata: Métadonnées additionnelles.

        Returns:
            Liste de DocumentChunk.
        """
        if not content or not content.strip():
            return []

        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        source_name = source_path.rstrip("/").split("/")[-1]

        # LA GRANULARITE EST UNE PROPRIETE DU DOCUMENT, PAS DE L'ESPACE.
        #
        # Un espace peut contenir cinquante PDF scannes et dix fichiers structures.
        # Decider par document donne a chacun le decoupage que sa forme permet, et le
        # cas mixte devient gratuit — sans que l'utilisateur ait rien a declarer.
        #
        #  force la decision quand la detection se trompe. C'est un dernier
        # recours, pas le mecanisme principal.
        par_article = False
        if doc_type == "md":
            if self._strategie == "article" or (
                    self._strategie == "auto"
                    and decoupage_par_article_pertinent(content)):
                raw_chunks = self._chunk_par_article(content)
                par_article = True
            else:
                raw_chunks = self._chunk_markdown(content)
        else:
            raw_chunks = self._chunk_text(content)

        # Post-traitement : fusionner les petits, découper les gros
        # UN ARTICLE NE SE FUSIONNE PAS AVEC SON VOISIN.
        #
        # `_postprocess` recolle les chunks trop courts. Applique aux articles, il
        # fondait trois articles en deux — et defaisait la propriete recherchee : un
        # passage, un article, une identite citable. Un article court reste un
        # article ; sa brievete est sa forme, pas un defaut de decoupage.
        #
        # Le decoupage des articles trop LONGS est conserve : lui protege le budget
        # de contexte, et ne detruit aucune identite.
        processed = self._postprocess(raw_chunks, fusionner=not par_article)

        chunks: list[DocumentChunk] = []
        for i, (text, section) in enumerate(processed):
            # Préfixer le texte avec la section pour enrichir le contexte de l'embedding
            embedded_text = f"[{section}]\n{text}" if section else text
            chunks.append(DocumentChunk(
                text=embedded_text,
                source_path=source_path,
                source_name=source_name,
                section=section,
                position=i,
                doc_type=doc_type,
                source_hash=source_hash,
                metadata=metadata or {},
            ))

        logger.debug(
            "chunked %s → %d chunks (type=%s)",
            source_name, len(chunks), doc_type,
        )
        return chunks

    def _chunk_par_article(self, contenu: str) -> list[tuple[str, str]]:
        """Un chunk par article, l'article portant sa propre identite.

        CE QUE CELA CHANGE, MESURE LE 01/09/2026 sur le corpus marches publics :

            | | par titre markdown | par article |
            | chunks             | 2388 | 1021 |
            | portant un article |  35 % |  74 % |

        Deux passages sur trois n'avaient aucun numero d'article, alors que le prompt
        de l'espace ordonne d'en citer un. D12 avait mesure le gain de recuperation :
        82 % contre 72 %, index 17 % plus petit.

        LE PREFIXE HIERARCHIQUE N'EST PAS REPRODUIT. La strategie du harnais ajoutait
        au texte le titre du document et sa position dans le code. D28 (23/08/2026) l'a
        mesure en isolant la variable : 89 cas complets AVEC, 90 SANS. Il ne sert pas au
        rappel. `chunk_document` prefixe deja la section, ce qui suffit — et ne pas le
        recopier retire la principale source de divergence entre les deux
        implementations.
        """
        blocs = re.split(r"(?=^##\s+Article\s+)", contenu, flags=re.M)
        sections: list[tuple[str, str]] = []
        for bloc in blocs:
            m = _TITRE_ARTICLE.match(bloc)
            if not m:
                continue
            titre = f"Article {m.group(1).strip()}"
            corps = bloc[m.end():].strip()
            if corps:
                sections.append((corps, titre))
        return sections

    def _chunk_markdown(self, content: str) -> list[tuple[str, str]]:
        """Découpe un markdown par sections de titres (#)."""
        # Split sur les lignes de titre
        sections: list[tuple[str, str]] = []
        current_title = ""
        current_lines: list[str] = []

        for line in content.split("\n"):
            if re.match(r"^#{1,6}\s+", line):
                # Nouveau titre — sauver la section précédente
                if current_lines:
                    text = "\n".join(current_lines).strip()
                    if text:
                        sections.append((text, current_title))
                current_title = line.strip().lstrip("#").strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        # Dernière section
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                sections.append((text, current_title))

        # Si les sections sont trop longues, les re-découper
        result: list[tuple[str, str]] = []
        for text, title in sections:
            if len(text) > MAX_CHUNK_SIZE:
                sub_chunks = self._sliding_window(text)
                result.extend((chunk, title) for chunk in sub_chunks)
            else:
                result.append((text, title))

        return result

    def _chunk_text(self, content: str) -> list[tuple[str, str]]:
        """Découpe un texte par paragraphes puis sliding window."""
        # Essayer de splitter par double saut de ligne (paragraphes)
        paragraphs = re.split(r"\n\s*\n", content)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        # Si un paragraphe unique est trop long, utiliser sliding window
        result: list[tuple[str, str]] = []
        buffer = ""

        for para in paragraphs:
            if len(buffer) + len(para) + 2 <= self._chunk_size:
                buffer = f"{buffer}\n\n{para}".strip() if buffer else para
            else:
                if buffer:
                    result.append((buffer, ""))
                if len(para) > self._chunk_size:
                    sub_chunks = self._sliding_window(para)
                    result.extend((chunk, "") for chunk in sub_chunks)
                else:
                    buffer = para
                    continue
                buffer = ""

        if buffer:
            result.append((buffer, ""))

        return result

    def _sliding_window(self, text: str) -> list[str]:
        """Découpe un texte long en fenêtres glissantes."""
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + self._chunk_size
            chunk = text[start:end]

            # Essayer de couper sur un espace ou saut de ligne
            if end < len(text):
                last_break = max(
                    chunk.rfind("\n"),
                    chunk.rfind(". "),
                    chunk.rfind(" "),
                )
                if last_break > self._chunk_size // 2:
                    chunk = chunk[: last_break + 1]
                    end = start + last_break + 1

            chunks.append(chunk.strip())
            start = end - self._chunk_overlap

        return [c for c in chunks if c]

    def _postprocess(self, chunks: list[tuple[str, str]],
                     fusionner: bool = True) -> list[tuple[str, str]]:
        """Fusionne les chunks trop petits, re-découpe les trop gros."""
        result: list[tuple[str, str]] = []
        buffer_text = ""
        buffer_section = ""

        for text, section in chunks:
            if len(text) < MIN_CHUNK_SIZE and fusionner:
                # Fusionner avec le précédent
                if buffer_text:
                    buffer_text = f"{buffer_text}\n\n{text}"
                else:
                    buffer_text = text
                    buffer_section = section
                continue

            # Flush le buffer si on en a un
            if buffer_text:
                combined = f"{buffer_text}\n\n{text}"
                if len(combined) <= MAX_CHUNK_SIZE:
                    buffer_text = combined
                    continue
                else:
                    result.append((buffer_text, buffer_section))
                    buffer_text = ""
                    buffer_section = ""

            if len(text) > MAX_CHUNK_SIZE:
                for sub in self._sliding_window(text):
                    result.append((sub, section))
            else:
                result.append((text, section))

        if buffer_text:
            result.append((buffer_text, buffer_section))

        return result
