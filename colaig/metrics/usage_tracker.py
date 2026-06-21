"""
Colaig — Suivi d'usage LLM par tenant (tokens + requêtes).

Accumulateur en mémoire, thread-safe (asyncio mono-thread), keyé par client_id.
Alimenté par les clients LLM (usage des réponses OpenAI-compatible), exposé via
/metrics (JSON) et /metrics/prometheus. Permet la facturation / observabilité
par tenant sans base de données.

Reconstructible au restart (cohérent avec le principe zéro-DB / cache éphémère).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

_GLOBAL = "_global"


@dataclass
class _Counters:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class UsageTracker:
    """Compteurs d'usage par client_id (+ agrégat global) + quotas journaliers.

    - Cumul (depuis le démarrage) : exposé en /metrics.
    - Fenêtre journalière par client : sert aux quotas (check_quota).
    Quotas : 0 = illimité. Override par client via per_client_limits[client_id].
    """

    _by_client: dict = field(default_factory=dict)
    _daily: dict = field(default_factory=dict)      # client_id -> _Counters (du jour)
    _day: object = None                              # date courante de la fenêtre
    daily_request_limit: int = 0                     # défaut global (0 = illimité)
    daily_token_limit: int = 0
    per_client_limits: dict = field(default_factory=dict)  # client_id -> {"requests", "tokens"}

    def set_limits(self, daily_request_limit: int = 0, daily_token_limit: int = 0,
                   per_client_limits: dict | None = None) -> None:
        """Configure les quotas journaliers (appelé au démarrage depuis la config)."""
        self.daily_request_limit = daily_request_limit
        self.daily_token_limit = daily_token_limit
        self.per_client_limits = per_client_limits or {}

    def _roll_day(self) -> None:
        today = date.today()
        if self._day != today:
            self._day = today
            self._daily = {}

    def _limits_for(self, client_id: str) -> tuple[int, int]:
        ov = self.per_client_limits.get(client_id, {})
        return (int(ov.get("requests", self.daily_request_limit)),
                int(ov.get("tokens", self.daily_token_limit)))

    def check_quota(self, client_id: str) -> tuple[bool, str]:
        """Retourne (autorisé, raison). True si pas de quota ou pas atteint."""
        self._roll_day()
        req_limit, tok_limit = self._limits_for(client_id or _GLOBAL)
        if not req_limit and not tok_limit:
            return True, ""
        c = self._daily.get(client_id or _GLOBAL)
        if c is None:
            return True, ""
        if req_limit and c.requests >= req_limit:
            return False, f"quota requêtes journalier atteint ({req_limit})"
        if tok_limit and c.total_tokens >= tok_limit:
            return False, f"quota tokens journalier atteint ({tok_limit})"
        return True, ""

    def record(self, client_id: str, prompt_tokens: int = 0,
               completion_tokens: int = 0) -> None:
        """Enregistre une requête LLM et ses tokens (cumul + fenêtre du jour)."""
        self._roll_day()
        for key in (_GLOBAL, client_id or _GLOBAL):
            c = self._by_client.setdefault(key, _Counters())
            c.requests += 1
            c.prompt_tokens += int(prompt_tokens or 0)
            c.completion_tokens += int(completion_tokens or 0)
        d = self._daily.setdefault(client_id or _GLOBAL, _Counters())
        d.requests += 1
        d.prompt_tokens += int(prompt_tokens or 0)
        d.completion_tokens += int(completion_tokens or 0)

    def record_from_usage(self, client_id: str, usage: dict | None) -> None:
        """Enregistre depuis le bloc `usage` d'une réponse OpenAI-compatible."""
        usage = usage or {}
        self.record(
            client_id,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def snapshot(self) -> dict:
        """Vue sérialisable : global + par client."""
        glob = self._by_client.get(_GLOBAL, _Counters())
        clients = {
            cid: {
                "requests": c.requests,
                "prompt_tokens": c.prompt_tokens,
                "completion_tokens": c.completion_tokens,
                "total_tokens": c.total_tokens,
            }
            for cid, c in self._by_client.items()
            if cid != _GLOBAL
        }
        return {
            "global": {
                "requests": glob.requests,
                "prompt_tokens": glob.prompt_tokens,
                "completion_tokens": glob.completion_tokens,
                "total_tokens": glob.total_tokens,
            },
            "by_client": clients,
        }

    def prometheus_text(self) -> str:
        """Exposition format texte Prometheus (sans dépendance externe)."""
        lines = [
            "# HELP colaig_llm_requests_total Nombre de requetes LLM.",
            "# TYPE colaig_llm_requests_total counter",
            "# HELP colaig_llm_tokens_total Tokens LLM consommes.",
            "# TYPE colaig_llm_tokens_total counter",
        ]
        for cid, c in self._by_client.items():
            if cid == _GLOBAL:
                continue
            lbl = cid.replace('"', '\\"')
            lines.append(f'colaig_llm_requests_total{{client="{lbl}"}} {c.requests}')
            lines.append(
                f'colaig_llm_tokens_total{{client="{lbl}",type="prompt"}} {c.prompt_tokens}'
            )
            lines.append(
                f'colaig_llm_tokens_total{{client="{lbl}",type="completion"}} {c.completion_tokens}'
            )
        return "\n".join(lines) + "\n"
