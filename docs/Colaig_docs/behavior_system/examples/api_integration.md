# Intégration d'une API Externe

## Introduction

Ce guide montre comment intégrer une API externe dans Colaig, en utilisant l'exemple d'une API météorologique.

## Processus d'Intégration

### 1. Analyse de l'API

Exemple avec l'API OpenWeatherMap :

```json
{
    "api_documentation": {
        "base_url": "https://api.openweathermap.org/data/2.5",
        "endpoints": {
            "current": "/weather",
            "forecast": "/forecast",
            "air_quality": "/air_pollution"
        },
        "authentication": {
            "type": "api_key",
            "location": "query_param",
            "param_name": "appid"
        }
    }
}
```

### 2. Configuration de l'Outil

```json
{
    "type": "tool",
    "description": "Client API météo",
    "priority": 0.8,
    "configuration": {
        "operations": {
            "get_weather": {
                "endpoint": "/weather",
                "method": "GET",
                "required_params": ["city"],
                "optional_params": {
                    "units": "metric",
                    "lang": "fr"
                }
            },
            "get_forecast": {
                "endpoint": "/forecast",
                "method": "GET",
                "required_params": ["city"],
                "optional_params": {
                    "days": 5,
                    "units": "metric"
                }
            }
        },
        "security": {
            "api_key": {
                "source": "env",
                "variable": "WEATHER_API_KEY"
            }
        },
        "error_handling": {
            "retry_policy": {
                "max_attempts": 3,
                "delay_seconds": 2
            }
        }
    }
}
```

### 3. Création de l'Action

```json
{
    "type": "action",
    "description": "Assistant météo",
    "priority": 0.85,
    "configuration": {
        "base": {
            "parameters": {
                "default_city": "Paris",
                "units": "metric",
                "language": "fr"
            }
        },
        "tools": {
            "weather_api": {
                "type": "api_tool",
                "config": {
                    "tool_name": "weather_client",
                    "required_operations": [
                        "get_weather",
                        "get_forecast"
                    ]
                }
            }
        },
        "prompts": {
            "base_prompt": "Je suis votre assistant météo. Je peux vous donner la météo actuelle et les prévisions.",
            "style_variations": {
                "brief": {
                    "template": "À {city}, il fait {temperature}°C, {conditions}."
                },
                "detailed": {
                    "template": "Météo à {city} :\n- Température : {temperature}°C\n- Conditions : {conditions}\n- Humidité : {humidity}%\n- Vent : {wind_speed} km/h"
                }
            }
        }
    }
}
```

### 4. Configuration des Prompts

```json
{
    "type": "prompt",
    "description": "Prompts pour l'assistant météo",
    "priority": 0.8,
    "configuration": {
        "conversation_flows": {
            "weather_request": {
                "initial": "Pour quelle ville souhaitez-vous connaître la météo ?",
                "city_confirmation": "Je vais chercher la météo pour {city}. Est-ce correct ?",
                "forecast_option": "Souhaitez-vous également les prévisions pour les prochains jours ?"
            },
            "error_handling": {
                "city_not_found": "Je ne trouve pas la ville {city}. Pouvez-vous vérifier l'orthographe ou proposer une ville proche ?",
                "api_error": "Désolé, je rencontre des difficultés pour obtenir les informations météo. Voulez-vous réessayer ?"
            }
        }
    }
}
```

### 5. Règles de Validation

```json
{
    "type": "rule",
    "description": "Règles pour l'assistant météo",
    "priority": 0.8,
    "configuration": {
        "validation": {
            "city_format": {
                "pattern": "^[A-Za-zÀ-ÿ\\s-]+$",
                "error_message": "Nom de ville invalide"
            },
            "rate_limits": {
                "requests_per_minute": 60,
                "error_message": "Trop de requêtes, veuillez patienter"
            }
        }
    }
}
```

## Mise en Place

### 1. Structure des Fichiers

```bash
.colaig/
├── actions/
│   └── weather_assistant.json
├── tools/
│   └── weather_api_client.json
├── prompts/
│   └── weather_prompts.json
└── rules/
    └── weather_rules.json
```

### 2. Configuration de l'Environnement

```bash
# .env
WEATHER_API_KEY=votre_clé_api
WEATHER_API_URL=https://api.openweathermap.org/data/2.5
```

### 3. Vérification de l'Installation

```python
async def verify_weather_integration():
    """
    Vérifie l'intégration avec :
    1. Test de connexion API
    2. Validation des configurations
    3. Test des fonctionnalités
    """
```

## Utilisation

### 1. Exemple Simple

```python
# Demande météo simple
user: "Quel temps fait-il à Paris ?"

# Réponse de Colaig
assistant: "🤖 À Paris, il fait 18°C, ciel partiellement nuageux.

Souhaitez-vous plus de détails ou les prévisions pour les prochains jours ?"
```

### 2. Exemple Détaillé

```python
# Demande détaillée
user: "Donnez-moi les prévisions détaillées pour Lyon pour les 3 prochains jours."

# Réponse de Colaig
assistant: "🤖 Voici les prévisions météo pour Lyon :

Aujourd'hui :
- Température : 20°C
- Conditions : Ensoleillé
- Humidité : 65%
- Vent : 12 km/h

Demain :
- Matin : 16°C, Nuageux
- Après-midi : 22°C, Éclaircies
- Soir : 18°C, Ciel dégagé

Après-demain :
- Matin : 15°C, Brume
- Après-midi : 21°C, Ensoleillé
- Soir : 17°C, Clair

Souhaitez-vous des informations supplémentaires ?"
```

## Personnalisation

### 1. Ajout de Fonctionnalités

```json
{
    "operations": {
        "get_air_quality": {
            "endpoint": "/air_pollution",
            "method": "GET",
            "required_params": ["lat", "lon"],
            "response_format": {
                "aqi": "number",
                "components": "object"
            }
        }
    }
}
```

### 2. Adaptation des Prompts

```json
{
    "style_variations": {
        "eco_friendly": {
            "template": "Météo à {city} :\n{conditions}\nQualité de l'air : {aqi_level}\nRecommandations : {eco_tips}"
        }
    }
}
```

## Bonnes Pratiques

### 1. Gestion des Erreurs

```python
async def handle_api_error(error: ApiError):
    """
    Gestion des erreurs avec :
    - Logging détaillé
    - Retry automatique
    - Fallback sur cache
    - Messages utilisateur appropriés
    """
```

### 2. Cache et Performance

```python
class WeatherCache:
    """
    Système de cache avec :
    - TTL adaptatif
    - Invalidation intelligente
    - Préchargement prédictif
    """
```

### 3. Sécurité

- Validation des entrées utilisateur
- Gestion sécurisée des clés API
- Rate limiting
- Logging des accès

## Maintenance

### 1. Surveillance

```python
async def monitor_weather_service():
    """
    Surveillance avec :
    - Métriques d'utilisation
    - Temps de réponse
    - Taux d'erreur
    - Utilisation du quota
    """
```

### 2. Mises à Jour

- Vérification régulière des changements d'API
- Tests de non-régression
- Documentation des modifications
- Plan de rollback 