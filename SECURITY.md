# Politique de sécurité

## Signaler une vulnérabilité

Merci de **ne pas** ouvrir d'issue publique pour une faille de sécurité.

Contactez en privé : **colaig@cerema.fr** (objet : `[SECURITE] Colaig`).
Décrivez la vulnérabilité, son impact et les étapes de reproduction.
Nous accusons réception sous quelques jours ouvrés et vous tenons informé
de la correction.

## Versions supportées

| Version | Supportée |
|---|---|
| 1.0.x | ✅ |
| < 1.0 | ❌ |

## Bonnes pratiques de déploiement

Voir [docs/SECURITE.md](docs/SECURITE.md) (modèle de menaces) et la checklist
« avant exposition publique » : activer l'auth, définir `COLAIG_PLATFORM_API_KEY`,
scanner les dépendances, faire un audit/pentest avant exposition.
