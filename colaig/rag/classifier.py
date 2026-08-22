"""
Colaig — ClassificationEngine (rag/classifier.py)

Moteur de classification documentaire inspiré de Archivist/ClassifyAgent.
Transposition adaptée à l'architecture Colaig v3 :
  - Zéro base de données : règles dans {workspace}/.colaig/rules/*.yaml
  - Async natif (pas Celery)
  - Opère sur DocumentRecord en mémoire
  - virtual_path = métadonnée de navigation (pas de déplacement physique par défaut)

Pipeline :
  ClassificationEngine.build_rules_context()
      ↓ contexte règles injecté dans le prompt Albert
  _call_albert_analysis() — Albert suggère virtual_path + virtual_filename
      ↓
  ClassificationEngine.classify() — confirme ou corrige par matching déterministe
      ↓
  DocumentRecord.virtual_path final + rule_applied + classification_confidence
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import yaml

from colaig.models import DocumentRecord
from colaig import paths

logger = logging.getLogger(__name__)

_RULES_SUBDIR = "rules"

# Variables standard disponibles sans configuration explicite dans identity.yaml.
# Toujours extraites par le LLM, toujours disponibles dans les templates.
_STANDARD_VARS: frozenset[str] = frozenset({
    "year", "month", "day", "date",
    "supplier", "customer", "amount", "location",
    "category", "filename", "ext",
})


# =============================================================================
# Modèles de règles (Zero-Database — instanciés depuis YAML)
# =============================================================================

class ClassificationCriteria:
    """Critères de matching d'une règle.

    Équivalent JSONB criteria dans Archivist ClassificationRule.
    """
    __slots__ = ("category", "entities", "keywords")

    def __init__(
        self,
        category: str = "",
        entities: dict | None = None,
        keywords: list[str] | None = None,
    ) -> None:
        self.category: str = category
        self.entities: dict = entities or {}
        self.keywords: list[str] = keywords or []

    def is_empty(self) -> bool:
        return not self.category and not self.entities and not self.keywords


class ClassificationAction:
    """Action appliquée quand une règle matche.

    Templates supportés : {supplier}, {year}, {month}, {day}, {date},
    {amount}, {customer}, {location}, {category}, {filename}, {ext},
    + toute variable custom issue de ai_entities.
    """
    __slots__ = ("virtual_path", "virtual_filename", "tags")

    def __init__(
        self,
        virtual_path: str = "",
        virtual_filename: str = "",
        tags: list[str] | None = None,
    ) -> None:
        self.virtual_path: str = virtual_path
        self.virtual_filename: str = virtual_filename
        self.tags: list[str] = tags or []

    def custom_vars(self) -> set[str]:
        """Variables custom référencées dans les templates, hors variables standard."""
        found: set[str] = set()
        for tmpl in (self.virtual_path, self.virtual_filename):
            if tmpl:
                found.update(re.findall(r"\{(\w+)\}", tmpl))
        return found - _STANDARD_VARS


class ClassificationRule:
    """Règle de classification déclarative — chargée depuis YAML.

    Stockée dans {workspace}/.colaig/rules/{slug}.yaml.

    Format YAML minimal :
        name: "Factures EDF"
        priority: 10
        criteria:
          category: "facture"
          entities:
            supplier: "EDF"
        action:
          virtual_path: "/Factures/{supplier}/{year}/"
          virtual_filename: "{date}_{supplier}_{amount}.pdf"

    Optionnel :
        enabled: true          # défaut : true
        tags: ["énergie"]      # ajoutés à DocumentRecord.ai_keywords
    """
    __slots__ = (
        "name", "criteria", "action",
        "enabled", "priority",
        "applied_count", "last_applied_at",
    )

    def __init__(
        self,
        name: str,
        criteria: ClassificationCriteria,
        action: ClassificationAction,
        enabled: bool = True,
        priority: int = 0,
        applied_count: int = 0,
        last_applied_at: datetime | None = None,
    ) -> None:
        self.name = name
        self.criteria = criteria
        self.action = action
        self.enabled = enabled
        self.priority = priority
        self.applied_count = applied_count
        self.last_applied_at = last_applied_at


# =============================================================================
# ClassificationEngine
# =============================================================================

class ClassificationEngine:
    """Moteur de classification documentaire.

    Chargé (lazy) par DocumentIndex lors du premier accès à un workspace.
    Applique les règles YAML après l'analyse Albert, dans analyze_pending().

    Usage :
        engine = ClassificationEngine(storage, workspace_path)
        await engine.load_rules()
        rules_ctx, extra_vars = engine.build_rules_context()
        # → injecter dans le prompt Albert
        record = await engine.classify(record)
    """

    def __init__(self, storage, workspace_path: str) -> None:
        self._storage = storage
        self._workspace_path = workspace_path.rstrip("/")
        self._rules: list[ClassificationRule] = []
        self._loaded = False

    # ── Interface publique ────────────────────────────────────────────

    async def load_rules(self) -> int:
        """Charge / recharge les règles depuis .colaig/rules/*.yaml.

        Returns:
            Nombre de règles activées.
        """
        rules_path = paths.instance_subdir(self._workspace_path, _RULES_SUBDIR)
        self._rules = []

        try:
            files = await self._storage.list_files(rules_path)
        except Exception:
            logger.debug("Aucun dossier rules à %s — classification désactivée", rules_path)
            self._loaded = True
            return 0

        for f in files:
            if f.is_directory or not f.name.endswith(".yaml"):
                continue
            try:
                raw = await self._storage.download(f.path)
                data = yaml.safe_load(raw.decode("utf-8"))
                rule = _parse_rule(data)
                if rule.enabled:
                    self._rules.append(rule)
            except Exception as exc:
                logger.warning("Impossible de charger la règle %s : %s", f.path, exc)

        # Trier par priorité décroissante — identique à Archivist
        self._rules.sort(key=lambda r: r.priority, reverse=True)
        self._loaded = True
        logger.info(
            "ClassificationEngine : %d règles chargées depuis %s",
            len(self._rules), rules_path,
        )
        return len(self._rules)

    def build_rules_context(self) -> tuple[str, set[str]]:
        """Construit le contexte règles à injecter dans le prompt Albert.

        Inspiré de AIAgent._build_rules_context() d'Archivist.
        Appelé par DocumentIndex._call_albert_analysis() avant de construire le prompt.

        Returns:
            (context_str, extra_vars)
            - context_str  : description textuelle des règles actives
            - extra_vars   : variables custom des templates à extraire en plus
        """
        if not self._rules:
            return "", set()

        lines: list[str] = []
        extra_vars: set[str] = set()

        for i, rule in enumerate(self._rules, 1):
            c = rule.criteria
            crit_parts: list[str] = []
            if c.category:
                crit_parts.append(f"category={c.category}")
            for key, val in c.entities.items():
                crit_parts.append(f"entity.{key}={val}")
            if c.keywords:
                crit_parts.append(f"keywords contient {c.keywords}")

            action_parts: list[str] = []
            if rule.action.virtual_path:
                action_parts.append(f"virtual_path: {rule.action.virtual_path}")
            if rule.action.virtual_filename:
                action_parts.append(f"virtual_filename: {rule.action.virtual_filename}")

            stats = f"appliquée {rule.applied_count}x" if rule.applied_count else "jamais appliquée"
            lines.append(
                f"Règle {i} '{rule.name}' (priorité {rule.priority}, {stats}): "
                f"SI {' ET '.join(crit_parts) or 'aucun critère'} "
                f"ALORS {', '.join(action_parts) or 'aucune action'}"
            )
            extra_vars.update(rule.action.custom_vars())

        return "\n".join(lines), extra_vars

    async def classify(self, record: DocumentRecord) -> DocumentRecord:
        """Applique les règles à un DocumentRecord post-analyse Albert.

        Inspiré de ClassifyAgent.run() d'Archivist.

        Ordre :
        1. La suggestion IA (record.virtual_path issu d'Albert) est sauvegardée
        2. Chaque règle est évaluée par ordre de priorité décroissante
        3. Première règle matchée → templates rendus → confidence 1.0
        4. Aucune règle → suggestion IA conservée → confidence 0.7
        5. Aucune suggestion → virtual_path = path physique → confidence 0.0

        Args:
            record: DocumentRecord après _call_albert_analysis().
                    record.ai_entities DOIT être un dict (pas une liste).

        Returns:
            DocumentRecord enrichi (virtual_path, virtual_filename,
            rule_applied, classification_confidence).
        """
        if not self._loaded:
            await self.load_rules()

        ai_suggestion_path = record.virtual_path   # peut être vide
        ai_suggestion_name = record.virtual_filename

        if not self._rules:
            # Aucune règle — conserver la suggestion IA si présente
            _set_ai_suggestion(record, ai_suggestion_path, ai_suggestion_name)
            return record

        for rule in self._rules:
            if not _evaluate_rule(record, rule):
                continue

            logger.info("Règle '%s' matchée pour %s", rule.name, record.name)
            variables = _extract_variables(record)

            # Appliquer templates d'action
            if rule.action.virtual_path:
                target_dir = _render(rule.action.virtual_path, variables)
                filename = (
                    _render(rule.action.virtual_filename, variables)
                    if rule.action.virtual_filename
                    else record.name
                )
                record.virtual_path = f"{target_dir.rstrip('/')}/{filename}"
                record.virtual_filename = filename
            elif rule.action.virtual_filename:
                record.virtual_filename = _render(rule.action.virtual_filename, variables)

            # Enrichir les tags (keywords)
            if rule.action.tags:
                existing = set(record.ai_keywords or [])
                record.ai_keywords = list(existing | set(rule.action.tags))

            record.rule_applied = rule.name
            record.classification_confidence = 1.0

            # Statistiques en mémoire (persistées au prochain save())
            rule.applied_count += 1
            rule.last_applied_at = datetime.now(UTC)

            return record

        # Aucune règle matchée
        _set_ai_suggestion(record, ai_suggestion_path, ai_suggestion_name)
        return record

    @property
    def rules(self) -> list[ClassificationRule]:
        """Liste des règles activées, ordre priorité décroissante."""
        return list(self._rules)

    @property
    def loaded(self) -> bool:
        return self._loaded


# =============================================================================
# Helpers internes — miroir de ClassifyAgent d'Archivist
# =============================================================================

def _evaluate_rule(record: DocumentRecord, rule: ClassificationRule) -> bool:
    """Évalue si une règle s'applique au record.

    Matching case-insensitive avec containment :
    "EDF" matche "EDF France Réseau", "*" matche toute valeur non vide.

    Identique à ClassifyAgent._evaluate_rule() d'Archivist.
    """
    c = rule.criteria
    if c.is_empty():
        logger.warning("Règle '%s' sans critères — ignorée", rule.name)
        return False

    # Catégorie
    if c.category:
        if not (record.ai_category and c.category.lower() in record.ai_category.lower()):
            return False

    # Entités — ai_entities EST un dict dans la version corrigée de DocumentRecord
    for key, expected in c.entities.items():
        if expected is None:
            continue
        actual = (record.ai_entities or {}).get(key)
        if expected == "*":
            if not actual:
                return False
        else:
            if not (actual and expected.lower() in str(actual).lower()):
                return False

    # Keywords
    for req_kw in c.keywords:
        doc_kw = [k.lower() for k in (record.ai_keywords or [])]
        if not any(req_kw.lower() in dk for dk in doc_kw):
            return False

    return True


def _extract_variables(record: DocumentRecord) -> dict:
    """Extrait les variables d'un DocumentRecord pour les templates.

    Identique à ClassifyAgent._extract_variables() d'Archivist.
    Ordre : défauts système → catégorie → entités AI → dérivation date.
    """
    now = datetime.now(UTC)
    variables: dict = {
        "year": str(now.year),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
        "date": now.strftime("%Y-%m-%d"),
        "filename": record.name,
        "ext": record.name.rsplit(".", 1)[-1] if "." in record.name else "",
    }

    if record.ai_category:
        variables["category"] = record.ai_category

    # Entités IA (dict, priorité sur les défauts système)
    entities = record.ai_entities or {}
    if isinstance(entities, dict):
        variables.update(entities)
    # Si ai_entities est encore une list[str] (migration), ignorer silencieusement

    # Dériver year/month/day depuis la date du document si disponible.
    # Ne dériver que si la date vient des entités (pas la date système).
    entity_date = isinstance(entities, dict) and entities.get("date")
    if entity_date:
        try:
            parsed = datetime.fromisoformat(str(entity_date))
            variables["year"] = str(parsed.year)
            variables["month"] = parsed.strftime("%m")
            variables["day"] = parsed.strftime("%d")
            variables["date"] = parsed.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    return {k: v for k, v in variables.items() if v is not None}


def _render(template: str, variables: dict) -> str:
    """Substitue les {variables} dans un template.

    Identique à ClassifyAgent._render_template() d'Archivist.
    Les variables non résolues sont supprimées (évite les chemins corrompus).
    """
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{key}}}", str(value))
    # Supprimer les {variables} non résolues
    result = re.sub(r"\{[^}]+\}", "", result)
    return result


def _set_ai_suggestion(
    record: DocumentRecord,
    ai_path: str,
    ai_name: str,
) -> None:
    """Applique la suggestion IA si présente, sinon repli sur le chemin physique."""
    if ai_path:
        record.virtual_path = ai_path
        if ai_name:
            record.virtual_filename = ai_name
        record.rule_applied = "ai_suggestion"
        record.classification_confidence = 0.7
    else:
        record.virtual_path = record.path
        record.virtual_filename = ""
        record.rule_applied = ""
        record.classification_confidence = 0.0


def _parse_rule(data: dict) -> ClassificationRule:
    """Désérialise un dict YAML en ClassificationRule."""
    c_data = data.get("criteria", {})
    a_data = data.get("action", {})

    criteria = ClassificationCriteria(
        category=c_data.get("category", ""),
        entities=c_data.get("entities", {}),
        keywords=c_data.get("keywords", []),
    )
    action = ClassificationAction(
        virtual_path=a_data.get("virtual_path", ""),
        virtual_filename=a_data.get("virtual_filename", ""),
        tags=a_data.get("tags", []),
    )
    return ClassificationRule(
        name=data.get("name", "unnamed"),
        criteria=criteria,
        action=action,
        enabled=data.get("enabled", True),
        priority=int(data.get("priority", 0)),
        applied_count=int(data.get("applied_count", 0)),
    )
