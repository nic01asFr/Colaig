# La frontière de confiance d'une instance Colaig

> **À lire avant de partager un espace de stockage avec Colaig.**
> Établi le 24/08/2026 sur le code du tronc, lot L2.1b. Chaque affirmation de ce
> document a été vérifiée dans le dépôt ; les points non vérifiés sont marqués comme tels.

## La topologie — de quoi on parle exactement

Colaig dispose de **son propre espace de stockage**. Un collègue **partage un dossier
depuis le sien** ; ce dossier apparaît à la racine de celui de Colaig, et devient un
espace de travail. Le collègue crée ensuite son salon Tchap et y invite qui de droit.

Dans le Bureau numérique du MTES, salon et dossier étaient créés **ensemble** depuis le
Nextcloud professionnel, portaient le même nom, et les politiques d'usage se
transposaient des droits de lecture et d'écriture de chacun.

Deux conséquences structurent tout ce document :

1. **Un partage porte un niveau de droit**, choisi par celui qui partage — lecture, ou
   lecture et écriture. Rien n'oblige un collègue à accorder l'écriture.
2. **La population du salon et celle du dossier sont la même**, par construction. Qui
   peut écrire dans le dossier est donc, en principe, qui est dans le salon.

Le code implémente cette topologie : `run_workspace_discovery_loop` (`main.py`) balaie
la racine à chaque cycle, adopte tout dossier portant un `.colaig/config.yaml`, et
**amorce lui-même** `.colaig/` pour ceux qui n'en ont pas. Un fichier `.colaig-ignore`
vaut exclusion explicite. La boucle est **opt-in** — `COLAIG_AUTO_DISCOVER_ENABLED`,
désactivée par défaut.

## En une phrase

**Écrire dans `.colaig/` d'un espace, c'est administrer l'assistant de cet espace.**
Colaig ne peut ni le contrôler ni le constater : la frontière de confiance est le
**partage de stockage**, et elle se pose à l'extérieur de Colaig.

## Pourquoi Colaig ne peut pas s'en charger

`StorageProtocol` (`colaig/protocols.py`) expose sept verbes : `list_files`, `download`,
`download_if_changed`, `upload`, `mkdir`, `exists`, `get_etag`, `delete`. **Aucune
notion d'ACL, de permission, ni de partage.**

Cela vaut dans les deux sens :

- Colaig ne peut pas **poser** de droits. Même quand c'est lui qui crée l'espace
  (`create_workspace`), il crée un dossier et écrit un `config.yaml` — il n'a aucun
  verbe pour restreindre qui d'autre y écrira.
- Colaig ne peut pas **constater** les droits. Il ne sait pas si l'espace qu'il lit est
  privé, partagé à deux, ou ouvert à toute une direction.

Ce n'est pas un oubli : c'est la conséquence du choix « provider-agnostic ». Un `ACL`
commun à WebDAV, S3, Box, Google Drive et MS Graph n'existe pas. Y remédier supposerait
de modifier `protocols.py`, ce qui relève d'un arbitrage humain (`CLAUDE.md` §5).

## La surface privilégiée — ce que « écrire dans `.colaig/` » permet exactement

Elle est plus large que les prompts.

| chemin | ce que son écriture donne |
|---|---|
| `.colaig/config.yaml` → `owners` | **s'ajouter comme administrateur de l'espace** |
| `.colaig/config.yaml` → `user_ids` | s'ouvrir l'accès à l'espace |
| `.colaig/config.yaml` → `mcp_connectors` | brancher un serveur MCP distant dont Colaig appellera les outils |
| `.colaig/config.yaml` → `system_prompt` | changer le comportement de l'assistant |
| `.colaig/prompts/{analyser,orchestrator,synthesiser}.md` | **remplacer intégralement** le prompt système d'un agent, en position prioritaire — avant le gabarit Colaig |
| `.colaig/skills/*.md` | injecter du texte dans le message système |
| `.colaig/behaviors/*.yaml` | orienter domaine, ton, vocabulaire |
| `.colaig/tasks/*.json` | faire exécuter des requêtes planifiées sous l'identité du créateur de la tâche |
| `.colaig/tokens/` | jetons d'accès MCP |

**Le cas le plus parlant.** `owners` est *délibérément* exclu de `_UPDATABLE` dans
`context/workspace.py`, avec ce commentaire : « pour éviter qu'un owner s'auto-promeuve
via l'outil d'update générique (anti-escalade de privilège) ». La garde est juste. Mais
elle protège une porte dont le mur n'existe que par le partage de stockage : qui peut
écrire `config.yaml` s'ajoute aux owners sans jamais passer par l'outil.

## Les deux modèles de provenance, et ce qu'ils garantissent réellement

### A — l'utilisateur crée l'espace et le partage avec Colaig

L'utilisateur administre les droits sur son propre stockage. C'est le bon endroit pour
le faire, et Colaig n'a pas à s'en mêler.

**Mais** : rien aujourd'hui n'avertit cet utilisateur que `.colaig/` est un dossier
privilégié. S'il partage un dossier d'équipe où douze personnes écrivent, les douze
administrent l'assistant. Colaig ne le détectera pas, et ne s'en plaindra pas.

### B — Colaig crée l'espace et le partage

Colaig crée le dossier avec son compte de service. **Il ne pose aucun droit** — il n'en
a pas le moyen. Qui d'autre peut y écrire dépend entièrement du backend et de la façon
dont le partage est ensuite constitué, hors de Colaig.

### Conclusion

Dans les deux cas, la maîtrise existe — **et elle est entièrement opérationnelle, jamais
technique du côté de Colaig.** L'affirmation « on peut maîtriser » est exacte comme
consigne d'exploitation ; elle serait fausse comme garantie du logiciel.

## Ce que Colaig garantit malgré tout

Une règle, et elle est tenue : **Colaig n'écrit jamais dans `.colaig/` pour le compte
d'un utilisateur.**

C'est ce qui distingue « l'espace configure son assistant » — assumé — de « n'importe
quel interlocuteur reconfigure l'assistant » — inacceptable. Trois chemins d'écriture
sont dirigés par l'utilisateur, et les trois sont contrôlés par
`security/path_validator.py` avec `allow_dotcolaig=False` :

- l'envoi de fichier MCP (`colaig_upload_file`) — contrôlé de longue date ;
- la livraison d'une tâche de fond en mode `document` — contrôlée **à la création et à
  la livraison** ; elle ne l'était **nulle part** avant le 24/08/2026 ;
- la création de document par l'orchestrateur (`create_document`) — elle ne l'était pas
  non plus. C'était le plus grave des deux : le chemin sort du **modèle**, dont les
  entrées comprennent les documents de l'espace. La chaîne complète de l'injection à la
  persistance tenait dans un fichier déposé, sans qu'aucun utilisateur ne demande rien.

Les autres écritures — index, conversations, mémoire, tâches, trame — construisent leur
chemin par `colaig/paths.py` à partir d'identifiants internes. Aucune n'accepte un chemin
venu de l'extérieur.

L'indexation ignore par ailleurs tout `.colaig/` : un fichier déposé là n'entre pas au
corpus.

## Recommandations d'exploitation

1. **Faire vivre `.colaig/` dans un dossier dont la liste des écrivains est la liste des
   administrateurs de l'assistant.** Si ce n'est pas le cas, considérez que tout écrivain
   de l'espace peut faire dire à l'assistant ce qu'il veut, à tous les autres.
2. **Un corpus large et partagé n'est pas un problème** : les documents sont du contenu
   non fiable *par construction*, balisés à l'entrée du prompt (D35). C'est le dossier
   d'instance qui porte l'autorité, pas le corpus.
3. **Un espace à écrivain unique** — un dossier que Colaig et un seul administrateur
   partagent — est le seul cas où le remplacement de prompt système est sûr sans
   convention externe.

## Lecture ou écriture — ce qui se passe réellement

### Un partage en lecture seule n'est pas un espace dégradé — il n'est pas un espace

C'est le comportement d'aujourd'hui, vérifié et désormais couvert par
`tests/test_partage_lecture_seule.py` :

> Colaig découvre le dossier, tente d'écrire `.colaig/config.yaml`, prend un 403, et
> **abandonne le dossier définitivement** — sans nouvel essai, pour ne pas marteler le
> serveur à chaque cycle.

C'est correct : sans dossier d'instance, il n'y a ni index, ni historique, ni mémoire.
Mais rien ne le disait, et le message ne le disait pas non plus — `create_workspace`
emballe l'erreur de droits dans un `WorkspaceConfigError`, et la distinction ne survit
que par le chaînage. Le journal nomme désormais la cause et le geste : *« Colaig n'a que
la LECTURE sur ce dossier… accordez-lui l'ÉCRITURE sur ce partage, ou déposez un fichier
`.colaig-ignore` pour assumer l'exclusion. »*

**Donc, à la question « lecture ou écriture ? » : aujourd'hui, écriture — sinon rien.**

### Une piste pour prendre en charge les deux : l'entrée et la sortie

Elle n'est pas retenue ici, elle est posée. Le partage entre le **corpus** et l'**état**
existe déjà en filigrane :

| | vit où | écrit par |
|---|---|---|
| documents, `.colaig/config.yaml`, `prompts/`, `skills/`, `behaviors/` | le dossier partagé | **les gens de l'espace** |
| index, conversations, mémoire, tâches, jetons, trame | *aujourd'hui le même dossier* | **Colaig seul** |

La configuration est une **entrée** ; l'état est une **sortie**. Les deux ne demandent ni
les mêmes droits ni la même confiance. Si la sortie vivait dans l'espace propre de
Colaig — où `/.colaig/federation/` existe **déjà** comme précédent —, alors :

- un partage en **lecture seule** suffirait, et deviendrait un mode de plein exercice ;
- personne d'autre que Colaig n'écrirait son état, ce qui referme la question du
  périmètre de confiance sans rien interdire à l'administrateur de l'espace ;
- l'écriture ne resterait requise que pour ce qui la mérite : livrer un document
  demandé, appliquer une auto-spécialisation.

Cela contredit la **lettre** du principe fondateur — « un espace + un dossier `.colaig`
= une instance complète » — en en servant peut-être mieux l'esprit. **Arbitrage humain
requis** ; rien n'est engagé dans cette direction.

### L'autre signal disponible, et inexploité

Salon et dossier partagent leur population. Colaig **voit** la composition du salon via
Matrix, et il ne s'en sert pas : `user_ids` et `owners` sont **déclarés** dans
`config.yaml`, jamais dérivés de l'appartenance au salon ni des droits de stockage.

C'est une piste, pas une recommandation : elle demanderait de vérifier que la
correspondance salon/dossier tient encore hors du Bureau numérique, ce qui **n'a pas été
mesuré**.

## Ce qui reste à trancher

**`storage_readonly` est une promesse non tenue.** Le champ existe sur `WorkspaceConfig`,
documenté « True si Colaig n'a que des droits de lecture ». **Un seul des vingt sites
d'écriture l'honore** (la trame de conversation). Index, conversations, mémoire
utilisateur, tâches, jetons écriraient quand même.

Et c'est structurel : le principe fondateur pose qu'« un espace de stockage + un dossier
`.colaig` = une instance complète ». Tout l'état de l'instance vit donc *dans* l'espace.
Un espace réellement en lecture seule n'est pas un mode dégradé, c'est un produit
différent — sauf à séparer le **corpus** (lisible, largement partagé) de l'**état
d'instance** (`.colaig/`, écrit par Colaig seul).

Cette séparation répondrait d'un coup aux deux questions — la frontière de confiance et
le « lecture seule ». Elle touche au principe fondateur, donc elle **n'est pas prise
ici**. Trois options se présentent :

1. **Tenir la promesse** : honorer `storage_readonly` partout, en acceptant qu'un espace
   en lecture seule perde index persistant, historique et mémoire.
2. **Découpler** : `.colaig/` d'un espace peut vivre ailleurs que dans l'espace. Contredit
   la lettre du principe fondateur, en sert peut-être mieux l'esprit.
3. **Retirer le champ** : un drapeau qui ne fait rien vaut moins que son absence, parce
   qu'il se lit comme une garantie.
