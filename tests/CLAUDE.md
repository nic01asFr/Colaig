# tests/ — contrat du harnais

## Règle unique

**Le harnais est déterministe et hors ligne.** Aucune horloge murale, aucun hasard non
semé, aucun accès réseau. Deux exécutions de la suite doivent produire exactement le
même résultat, dans le même processus comme dans un autre.

Ce n'est pas une exigence de confort. Un harnais non déterministe ne produit pas des
tests un peu moins fiables : il produit des tests **intermittents**, dont on finit par
accuser la CI plutôt que le code. Et sans référence reproductible, « ça a l'air mieux »
remplace la mesure — c'est exactement ce que ce chantier cherche à éviter.

## Où se trouve quoi

| fichier | rôle |
|---|---|
| `tests/fakes.py` | les trois doublures : `FakeStorage`, `FakeMessaging`, `FakeLLM` |
| `tests/conftest.py` | **point d'entrée unique** : fixtures, réexport des doublures |
| `tests/test_harnais.py` | contrat du harnais — déterminisme et conformité aux Protocols |

Il n'y a qu'un seul `conftest.py` dans le dépôt, et c'est voulu.

## Les trois doublures

```python
def test_quelque_chose(fake_storage, fake_messaging, fake_llm):
    ...
```

### FakeStorage — `StorageProtocol`

Tout en mémoire. Deux propriétés qui comptent :

- **L'etag est le SHA-256 du contenu.** Stable entre processus, et identique pour un
  contenu identique — comme un vrai backend.
- **Aucune horloge.** `last_modified` dérive d'un instant fixe et d'un compteur : les
  écritures s'ordonnent de façon reproductible.

> **Ne jamais revenir à `hash(content)`.** C'était l'implémentation avant le lot L0.4.
> `hash()` sur des `bytes` est randomisé par processus : mesuré, `hash(b'contenu')`
> donnait `2598434101455927999` puis `-123023570338129182` sur deux exécutions. Or
> l'indexation incrémentale repose entièrement sur la comparaison d'etags
> (`.colaig/indexes/etags.json`) — une doublure dont les etags bougent ne peut pas
> servir à tester ce mécanisme. `test_etag_stable_entre_processus` relance un
> interpréteur pour le vérifier ; un `assert` dans le processus courant ne dirait rien.

`storage.appels` journalise les I/O — on peut assertionner qu'un fichier a bien été lu,
sans mock.

### FakeMessaging — `MessagingProtocol`

Enregistre les envois (`envois`, `textes_envoyes()`, `dernier_envoi`) et les indicateurs
de frappe. `injecter(message)` déclenche le callback posé par `on_message` : c'est ce
qui permet de piloter la réception sans réseau.

> **À préférer à `AsyncMock()`.** Un `AsyncMock` accepte n'importe quel appel : il ne
> peut donc pas détecter qu'un appelant s'est trompé de signature, et un test peut
> passer sans avoir rien exercé. `injecter()` sans `on_message` préalable échoue
> franchement, plutôt que de ne rien faire en silence.

`run()` rend la main immédiatement : une boucle infinie dans un test fait pendre la suite.

### FakeLLM — `LLMClientProtocol`

- `chat()` : réponses scriptées via `chat_responses`, servies dans l'ordre puis répétées.
- `chat_with_tools()` : `tool_call_responses` pour éprouver la boucle agent.
- `embed()` : vecteur normalisé L2, dérivé d'un SHA-256 du texte. **Même texte, même
  vecteur**, dans ce processus comme dans le suivant.

`embedding_dim` vaut 384 par défaut, pour la vitesse. Les endpoints réels mesurés
servent du **4096** (Albert `qwen3-vl-embedding-8b`, SSPCloud `qwen3-embedding-8b`) :
un test de dimensionnement mémoire doit fixer `embedding_dim` explicitement, jamais
hériter du défaut.

## Compatibilité

`MockStorage`, `MockWebDAVClient` et `MockAlbertClient` restent des alias de
`FakeStorage`, `FakeStorage` et `FakeLLM`. Les tests existants fonctionnent sans
modification et héritent du déterminisme. Le nom canonique est `Fake*`.

## Exécuter

```bash
python -m pytest -q
```

**1778 tests, 33 s** (110 `skip`). Le critère du lot L0.4 est « suite complète hors
ligne < 60 s ».

`--ignore=tests/test_live.py` n'est plus nécessaire. Ses 41 tests exigeaient une instance
en écoute et **échouaient** sur un dépôt sain : un `pytest` nu sortait 41 rouges pour une
raison d'environnement. Ils **skippent** désormais, avec le motif et l'action à mener
(D14). Une suite dont on sait qu'elle est rouge « pour de mauvaises raisons » cesse
d'être lue, et le jour où un vrai défaut s'y ajoute, personne ne le voit.

Effet de bord mesurable : la suite est passée de 195 s à 33 s, parce que 41 tests
n'attendent plus un délai réseau.

## Deux tests qui gardent les autres

- `test_paths_source_unique.py` — aucun chemin `.colaig/` construit hors de `paths.py`,
  et aucun dossier concaténé avec un `/` qui produirait un double slash.
- `test_pas_de_secret_commite.py` — aucune forme de secret dans les fichiers **suivis
  par git**. Le dépôt est public ; un secret commité ne se rattrape pas par une
  suppression.

Chacun contient un test qui prouve qu'il **sait échouer**. Un garde-fou qu'on n'a
jamais vu se déclencher ne vaut rien — les deux étaient d'ailleurs verts pour de
mauvaises raisons avant qu'on le vérifie.
