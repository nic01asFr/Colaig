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
    """Compteurs d'usage par client_id (+ agrégat global)."""

    _by_client: dict = field(default_factory=dict)

    def record(self, client_id: str, prompt_tokens: int = 0,
               completion_tokens: int = 0) -> None:
        """Enregistre une requête LLM et ses tokens pour un client."""
        for key in (_GLOBAL, client_id or _GLOBAL):
            c = self._by_client.setdefault(key, _Counters())
            c.requests += 1
            c.prompt_tokens += int(prompt_tokens or 0)
            c.completion_tokens += int(completion_tokens or 0)

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
