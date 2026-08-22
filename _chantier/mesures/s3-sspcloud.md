# Stockage S3 SSPCloud — mesure du 22/08/2026

Bucket `nicolaslaval`, endpoint `https://minio.lab.sspcloud.fr`.
Credentials STS frappées via `AssumeRoleWithWebIdentity` à partir du jeton OIDC
(audience `minio-datanode`, policy `stsonly`), validité 7 jours.

**Point de mesure : poste de travail Windows, via internet.** Ce n'est pas le point de
mesure de la production. Les chiffres ci-dessous sont donc un **majorant** : depuis un
pod du datalab, la latence ne peut qu'être inférieure. C'est ce qui rend le verdict
solide — le seuil est franchi confortablement même dans le cas défavorable.

## Latence

| opération | médiane | échantillon |
|---|---|---|
| LIST non récursif (racine) | **32 ms** | 3 mesures |
| LIST non récursif (espace) | **31 ms** | 3 mesures |
| LIST récursif (espace) | **641 ms** | 14 objets seulement |
| GET d'un objet | **47 ms** | 6 mesures, min 31 / max 47 |
| PUT (1 Ko) | **86 ms** | 6 mesures, min 62 / max 375 |
| DELETE | **31 ms** | 6 mesures, min 31 / max 47 |

**Correction d'une lecture hâtive.** Le premier PUT mesuré donnait 437 ms, ce qui
laissait croire à une asymétrie forte entre lecture et écriture. Répété six fois, il
retombe à 86 ms de médiane : les 437 ms étaient l'établissement de connexion TLS, pas le
coût d'écriture. Un chiffre unique ne vaut pas mesure.

## Volumétrie de l'espace échantillon

`qgis-workspace/` : 14 objets, 203,4 Mo. Aucun marqueur `.albert` ni `.colaig`,
0 conversation — ce bucket ne contient pas de données Colaig.

## Verdict

**H3 est levée pour les opérations unitaires.** 31 ms en listing non récursif, très en
deçà du seuil de 300 ms au-delà duquel l'architecture de cache actuelle ne suffirait
plus. Le budget de 10 s d'une réponse n'est pas menacé par le stockage : sur cette
mesure, le poste dominant reste le LLM (1,19 s par tour outillé sur `qwen3-6-35b-moe`).

**Ce qui reste INCONNU, et qui compte.** Le listing récursif a été mesuré sur
**14 objets**. C'est précisément l'opération qui faisait exploser les timeouts de la
version déployée, et 641 ms sur 14 objets ne dit **rien** de son comportement sur un
corpus réel. Extrapoler serait exactement l'erreur que ce chantier cherche à éviter.

À remesurer sur un espace représentatif avant tout arbitrage sur l'indexation. La règle
posée dans la sonde reste valable et non vérifiée : si le listing récursif dépasse 10 s,
il faut l'interdire dans le code et n'indexer qu'en incrémental par ETags.

## Innocuité vérifiée

La sonde écrit sous `.colaig-probe/` et supprime la seule clé qu'elle a écrite. Contrôle
après six itérations : **aucun objet résiduel**. `qgis-workspace/` n'a été lu, jamais
modifié.
