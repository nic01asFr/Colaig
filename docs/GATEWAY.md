# Architecture Gateway — Colaig comme service universel

## Vision

Colaig est un **cerveau IA** accessible depuis n'importe quel canal de communication. Le Gateway est un service indépendant qui fait le pont entre les canaux et le cerveau Colaig via MCP.

```
                    ┌─────────────────────────┐
                    │     Canaux entrants      │
                    │                          │
                    │  Tchap/Matrix ──┐        │
                    │  Slack ─────────┤        │
                    │  Teams ─────────┤        │
                    │  Email ─────────┤        │
                    │  Téléphone ─────┤        │
                    │  Web Chat ──────┤        │
                    │  API REST ──────┘        │
                    └──────────┬──────────────┘
                               │
                               ▼
                    ┌─────────────────────────┐
                    │       GATEWAY            │
                    │                          │
                    │  Normalisation messages  │
                    │  Routing                 │
                    │  Rate limiting           │
                    │  Auth                    │
                    └──────────┬──────────────┘
                               │ MCP (Streamable HTTP)
                               ▼
                    ┌─────────────────────────┐
                    │     COLAIG (cerveau)     │
                    │                          │
                    │  Pipeline agents         │
                    │  RAG + Index             │
                    │  Storage Protocol        │
                    │  MCP Server /mcp         │
                    └─────────────────────────┘
```

## Pourquoi un Gateway séparé ?

1. **Colaig reste simple** — un seul container, zéro dépendance sur les canaux
2. **Scalabilité indépendante** — le Gateway peut être répliqué horizontalement
3. **Canaux multiples** — ajouter un canal = ajouter un adaptateur, pas modifier Colaig
4. **Sécurité** — le Gateway gère l'authentification et le rate limiting, Colaig ne voit que des requêtes MCP

## Architecture Matrix comme bus universel

Matrix est un protocole de messagerie décentralisé et fédéré. Il peut servir de **bus de messages universel** :

```
Tchap (natif Matrix) ────────────────┐
                                     │
Slack ──→ Matrix Bridge (mautrix) ───┤
                                     ├──→ Matrix Homeserver ──→ Gateway ──→ Colaig
Teams ──→ Matrix Bridge (mautrix) ───┤
                                     │
Email ──→ Matrix Bridge (postmoogle)─┘
```

### Avantages

- **Protocole unique** : le Gateway n'a besoin que d'un client Matrix
- **Bridges matures** : mautrix-slack, mautrix-teams, mautrix-whatsapp, postmoogle (email)
- **Fédération** : Colaig peut communiquer entre instances Matrix différentes
- **Souveraineté** : Matrix est open source, les serveurs sont auto-hébergés

### Inconvénients

- **Latence** : un bridge ajoute un hop
- **Complexité opérationnelle** : il faut maintenir les bridges
- **Limitations des bridges** : certaines features spécifiques aux plateformes sont perdues

## Alternative : adaptateurs directs

Pour les cas où la latence ou la richesse fonctionnelle prime :

```
Slack ──→ Slack Adapter ──→ Gateway ──→ Colaig (MCP)
Teams ──→ Teams Adapter ──→ Gateway ──→ Colaig (MCP)
Email ──→ SMTP Adapter ──→ Gateway ──→ Colaig (MCP)
Phone ──→ Voix Adapter ──→ Gateway ──→ Colaig (MCP)
```

Chaque adaptateur :
- Écoute sur le protocole natif du canal
- Normalise le message en format MCP (via `colaig_ask`)
- Envoie la réponse sur le canal d'origine

## MCP Bridge

Le Gateway peut aussi exposer Colaig comme **client MCP** vers d'autres serveurs MCP. Cela permet à Colaig d'utiliser des outils externes :

```
Colaig ──→ MCP Client ──→ Serveur MCP externe (GitHub, Jira, etc.)
```

C'est prévu pour Phase 5 — l'Orchestrateur dispatche les steps `mcp_tool` vers des serveurs MCP externes.

## Implémentation prévue (Phase 5)

Le Gateway est un **projet séparé** de Colaig. Il :
- Se connecte au endpoint MCP de Colaig (`/mcp`)
- Utilise les tools MCP (`colaig_ask`, `colaig_search`, etc.)
- Gère la session utilisateur et le routing
- Est stateless (tout l'état est dans Colaig via StorageProtocol)

```python
# Pseudo-code du Gateway
class GatewayAdapter:
    """Interface pour un adaptateur de canal."""
    async def listen(self): ...
    async def send(self, channel_id, message): ...

class MatrixAdapter(GatewayAdapter):
    """Adaptateur Matrix/Tchap."""
    ...

class SlackAdapter(GatewayAdapter):
    """Adaptateur Slack."""
    ...

class Gateway:
    def __init__(self, colaig_mcp_url, adapters):
        self.mcp_client = MCPClient(colaig_mcp_url)
        self.adapters = adapters

    async def handle_message(self, adapter, channel_id, text):
        result = await self.mcp_client.call_tool("colaig_ask", {
            "question": text,
            "conversation_id": channel_id,
        })
        await adapter.send(channel_id, result["answer"])
```

## Roadmap

| Phase | Composant | Statut |
|-------|-----------|--------|
| Phase 4 | MCP Server Colaig (`/mcp`) | Implémenté |
| Phase 5 | Gateway service (projet séparé) | Architecture documentée |
| Phase 5 | Matrix adapter (migration depuis messaging/matrix.py) | Planifié |
| Phase 5 | MCP Bridge (Colaig comme client MCP) | Planifié |
| Phase 6 | Adaptateurs voix/email/téléphone | Futur |
