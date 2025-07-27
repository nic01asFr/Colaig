# API Tchap

L'API Tchap est utilisée pour la communication avec la messagerie Tchap, permettant l'envoi et la réception de messages, ainsi que la gestion des salles.

## Points d'Entrée de l'API

### Base URL
```
https://api.tchap.gouv.fr
```

### Authentification
```http
Authorization: Bearer votre_token_tchap
```

## Endpoints

### 1. Authentification

#### Login
```http
POST /_matrix/client/r0/login
Content-Type: application/json

{
    "type": "m.login.password",
    "identifier": {
        "type": "m.id.thirdparty",
        "medium": "email",
        "address": "utilisateur@gouv.fr"
    },
    "password": "votre_mot_de_passe",
    "initial_device_display_name": "COLAIG Bot"
}
```

Réponse :
```json
{
    "user_id": "@utilisateur:tchap.gouv.fr",
    "access_token": "votre_token_access",
    "device_id": "ABCDEFGHIJ",
    "home_server": "tchap.gouv.fr"
}
```

### 2. Messages

#### Envoi de Message
```http
PUT /_matrix/client/r0/rooms/{roomId}/send/m.room.message/{txnId}
Content-Type: application/json

{
    "msgtype": "m.text",
    "body": "Votre message",
    "format": "org.matrix.custom.html",
    "formatted_body": "<p>Votre message</p>"
}
```

Réponse :
```json
{
    "event_id": "$event_id"
}
```

#### Récupération des Messages
```http
GET /_matrix/client/r0/rooms/{roomId}/messages?from={token}&dir=b&limit=50
```

Réponse :
```json
{
    "chunk": [
        {
            "content": {
                "body": "Message texte",
                "msgtype": "m.text"
            },
            "event_id": "$event_id",
            "origin_server_ts": 1677858242,
            "sender": "@utilisateur:tchap.gouv.fr",
            "type": "m.room.message"
        }
    ],
    "start": "t47409-4357353_219380_54332",
    "end": "t47429-4357353_219380_54332"
}
```

### 3. Salles

#### Création de Salle
```http
POST /_matrix/client/r0/createRoom
Content-Type: application/json

{
    "visibility": "private",
    "name": "Nom de la salle",
    "topic": "Description de la salle",
    "preset": "private_chat",
    "initial_state": [
        {
            "type": "m.room.guest_access",
            "state_key": "",
            "content": {
                "guest_access": "forbidden"
            }
        }
    ],
    "invite": ["@utilisateur2:tchap.gouv.fr"]
}
```

Réponse :
```json
{
    "room_id": "!room:tchap.gouv.fr"
}
```

#### Liste des Salles
```http
GET /_matrix/client/r0/joined_rooms
```

Réponse :
```json
{
    "joined_rooms": [
        "!room1:tchap.gouv.fr",
        "!room2:tchap.gouv.fr"
    ]
}
```

### 4. Synchronisation

#### Sync
```http
GET /_matrix/client/r0/sync?timeout=30000&since=s72594_4483_1934
```

Réponse :
```json
{
    "next_batch": "s72595_4483_1934",
    "rooms": {
        "join": {
            "!room:tchap.gouv.fr": {
                "timeline": {
                    "events": [
                        {
                            "type": "m.room.message",
                            "content": {
                                "body": "Nouveau message",
                                "msgtype": "m.text"
                            },
                            "event_id": "$event_id",
                            "sender": "@utilisateur:tchap.gouv.fr"
                        }
                    ]
                }
            }
        }
    }
}
```

## Limites et Rate Limiting

### Limites Générales
- Taille maximale des messages : 65536 caractères
- Nombre maximum de salles : Illimité
- Taille maximale des fichiers : 100 MB

### Rate Limiting
```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1677858242
```

- Messages : 100 requêtes/minute
- Création de salles : 10 requêtes/minute
- Sync : 1 requête/seconde

## Gestion des Erreurs

### Format des Erreurs
```json
{
    "errcode": "M_FORBIDDEN",
    "error": "You are not invited to this room.",
    "soft_logout": false
}
```

### Codes d'Erreur Communs

| Code | Description |
|------|-------------|
| `M_FORBIDDEN` | Accès interdit |
| `M_UNKNOWN` | Erreur inconnue |
| `M_UNKNOWN_TOKEN` | Token invalide |
| `M_MISSING_TOKEN` | Token manquant |
| `M_LIMIT_EXCEEDED` | Rate limit dépassé |
| `M_ROOM_NOT_FOUND` | Salle non trouvée |

## Implémentation

### Initialisation du Client
```python
from colaig.tools.tchap_client import TchapClient

client = TchapClient(
    base_url="https://api.tchap.gouv.fr",
    access_token="votre_token",
    user_id="@utilisateur:tchap.gouv.fr"
)
```

### Envoi de Message
```python
async def send_message(
    self,
    room_id: str,
    content: Union[str, Dict],
    message_type: str = "m.text"
) -> str:
    try:
        # Génération de l'ID de transaction
        txn_id = str(int(time.time() * 1000))
        
        # Formatage du contenu
        if isinstance(content, str):
            message_content = {
                "msgtype": message_type,
                "body": content,
                "format": "org.matrix.custom.html",
                "formatted_body": f"<p>{content}</p>"
            }
        else:
            message_content = content
        
        # Envoi du message
        response = await self._put(
            f"/rooms/{room_id}/send/m.room.message/{txn_id}",
            json=message_content
        )
        
        return response["event_id"]
        
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi du message: {e}")
        raise
```

### Récupération des Messages
```python
async def get_messages(
    self,
    room_id: str,
    limit: int = 50,
    from_token: Optional[str] = None
) -> List[TchapMessage]:
    try:
        # Construction des paramètres
        params = {
            "dir": "b",
            "limit": limit
        }
        if from_token:
            params["from"] = from_token
        
        # Récupération des messages
        response = await self._get(
            f"/rooms/{room_id}/messages",
            params=params
        )
        
        # Conversion en objets TchapMessage
        messages = []
        for event in response["chunk"]:
            if event["type"] == "m.room.message":
                messages.append(TchapMessage(
                    id=event["event_id"],
                    room_id=room_id,
                    sender=event["sender"],
                    content=event["content"],
                    timestamp=event["origin_server_ts"]
                ))
        
        return messages
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des messages: {e}")
        raise
```

### Synchronisation
```python
async def sync(
    self,
    timeout: int = 30000,
    since: Optional[str] = None
) -> Dict:
    try:
        # Construction des paramètres
        params = {"timeout": timeout}
        if since:
            params["since"] = since
        
        # Appel de sync
        response = await self._get("/sync", params=params)
        
        # Traitement des événements
        for room_id, room_data in response.get("rooms", {}).get("join", {}).items():
            for event in room_data.get("timeline", {}).get("events", []):
                if event["type"] == "m.room.message":
                    await self._handle_message(room_id, event)
        
        return response
        
    except Exception as e:
        logger.error(f"Erreur lors de la synchronisation: {e}")
        raise
```

## Bonnes Pratiques

1. **Gestion des Sessions**
   - Implémenter un refresh token automatique
   - Gérer les déconnexions proprement
   - Maintenir une session active

2. **Synchronisation**
   - Utiliser le long polling avec timeout
   - Gérer les reconnexions
   - Traiter les événements de manière asynchrone

3. **Messages**
   - Valider le contenu avant envoi
   - Gérer le formatage HTML
   - Implémenter une file d'attente

4. **Sécurité**
   - Valider les tokens régulièrement
   - Vérifier les permissions
   - Nettoyer les données sensibles 