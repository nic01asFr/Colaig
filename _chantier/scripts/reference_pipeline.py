"""
La référence, mesurée sur l'ASSISTANT et non sur son cœur RAG.

STATUT: COMPLET
VERSION: 2026-08-30 - v1.0
LOT: L1.5 (instrumentation du pipeline agent)

L'ANGLE MORT QUE CE HARNAIS FERME
-----------------------------------
`reference_generation.py` appelle `generator.py` **directement**. Il ne passe ni par
l'Analyseur, ni par l'Orchestrateur, ni par le Synthétiseur — et retrouve donc toujours
ses passages, avec un `k` fixe et sans aucune décision d'agent.

**Tout ce qui vit dans le pipeline agent est donc hors de la surface mesurée** :
`needs_rag`, le filtrage d'outils, la boucle d'orchestration, le choix des sources, la
synthèse conditionnelle. La moitié de l'assistant n'est pas instrumentée, et la porte P2
jugerait d'un système qu'elle ne voit qu'à moitié.

Constaté le 30/08/2026 en retirant la porte `needs_rag` : le lot était impossible à
mesurer contre la référence, alors qu'il portait précisément sur ce que la référence ne
regarde pas.

CE QUI EST RÉUTILISÉ, ET POURQUOI TOUT L'EST SAUF UNE FONCTION
----------------------------------------------------------------
Ce harnais **exécute `reference_generation.py`** et n'y remplace qu'une chose :
`repondre()`, qui produit le texte. Tout le reste — corpus, découpage, embeddings,
recherche, jeu doré, notation, rapport, nommage des fichiers — est **le même objet en
mémoire**, pas une copie.

C'est délibéré et c'est la condition de la comparaison : deux harnais qui noteraient
« pareil » divergeraient au premier correctif, et l'on comparerait alors deux mesures qui
n'ont pas la même règle. Ce dépôt a déjà payé cinq fois la copie d'un motif.

CE QUE LE CHIFFRE VOUDRA DIRE
-------------------------------
Le cœur RAG répond juste à **92,2 %** (pile de production, 30/08). Si l'assistant fait
moins, **le pipeline soustrait** — et l'on saura de combien. S'il fait autant, les
décisions d'agent sont neutres sur ce jeu, ce qui est aussi une information.

    COLAIG_REF_K=10 COLAIG_REF_RAISONNEMENT=0 \
    COLAIG_REF_EMBED_MODELE=qwen3-embedding-8b COLAIG_REF_EMBED_DIM=4096 \
    COLAIG_REF_EMBED_BASE=https://llm.lab.sspcloud.fr/api/v1 \
    python _chantier/scripts/reference_pipeline.py durci
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE))

SCRIPTS = RACINE / "_chantier" / "scripts"

# On charge `reference_generation` SANS le lancer : `main()` ne doit tourner qu'après la
# substitution. Le module lit lui-même `sys.argv`, on le lui laisse tel quel.
SRC = (SCRIPTS / "reference_generation.py").read_text(encoding="utf-8")
_GEN: dict = {"__name__": "refgen",
              "__file__": str(SCRIPTS / "reference_generation.py")}
exec(compile(SRC.replace("raise SystemExit(main())", "pass"),  # noqa: S102
             "reference_generation.py", "exec"), _GEN)

sys.path.insert(0, str(SCRIPTS))
# `LLMDistant` n'est pas defini par reference_generation : il vient du harnais qui
# l'a introduit, comme dans mesure_utilite_trame. Une seconde implementation ferait
# diverger la mesure de son temoin — c'est le meme client, pas un equivalent.
from mesure_ancre_empoisonnee import LLMDistant  # noqa: E402

from colaig.agents.analyser import Analyser  # noqa: E402
from colaig.agents.orchestrator import Orchestrator  # noqa: E402
from colaig.agents.synthesiser import Synthesiser  # noqa: E402
from colaig.models import (  # noqa: E402
    ContextMode,
    IncomingMessage,
    SearchResult,
    WorkspaceConfig,
    WorkspaceContext,
)
from tests.fakes import FakeStorage  # noqa: E402


class _LLMPipeline:
    """Le client que les trois agents partagent, vers le vrai endpoint.

    Il délègue à celui de la référence : même endpoint, même modèle, même réglage de
    raisonnement. Une seconde implémentation ferait diverger la mesure de son témoin.
    """

    def __init__(self, cle_api: str) -> None:
        self._interne = LLMDistant(cle_api)
        self.model_embed = ""

    async def chat(self, messages, model=None, temperature=0.3, max_tokens=2048,
                   priority="user"):
        return await self._interne.chat(messages, model=model, temperature=temperature,
                                        max_tokens=max_tokens, priority=priority)

    async def chat_with_tools(self, messages, tools=None, tool_choice="auto",
                              model=None, temperature=0.3, max_tokens=2048,
                              priority="user"):
        """Appelle vraiment l'endpoint avec les outils, quand on lui en donne.

        CE QUE CETTE METHODE EMPECHAIT DE MESURER.

        Elle deleguait a `chat()` et rendait `tool_calls=[]` — au motif que le jeu dore
        est documentaire. C'est juste pour les outils METIER : aucun cas n'attend une
        action, et les rendre disponibles mesurerait la boucle d'outils.

        Mais l'Analyseur s'en sert pour autre chose : `COLAIG_ANALYSER_USE_TOOL_CALLING`
        lui fait produire son Intent par un appel d'outil, donc en JSON garanti, au lieu
        de le rediger en texte libre et de le parser. Avec l'ancienne version, activer
        ce mode faisait recevoir `tool_calls=[]` a l'Analyseur, qui repliait — et les
        deux bras auraient rendu le meme resultat.

        C'est le motif exact releve dans `mesure_ancre_empoisonnee` : « quatre tirages
        sur quatre repliaient, et les DEUX bras rendaient 0 % de bascule. Un resultat
        parfaitement rassurant qui ne mesurait rien. »

        Sans `tools`, le comportement ne change pas : un appel simple.
        """
        from colaig.models import ChatCompletionResult

        if not tools:
            texte = await self.chat(messages, model=model, temperature=temperature,
                                    max_tokens=max_tokens, priority=priority)
            return ChatCompletionResult(content=texte, tool_calls=[])
        return await self._interne.chat_avec_outils(
            messages, tools, tool_choice, temperature, max_tokens)


class _RetrieverFige:
    """Un retriever qui rend les passages DÉJÀ trouvés par la référence.

    La recherche n'est pas ce qu'on mesure ici : elle l'est déjà, et à l'identique, par
    `reference_l15.py`. Figer les passages isole ce qui change — les décisions d'agent —
    et rend l'écart avec le cœur RAG imputable à elles seules.
    """

    def __init__(self) -> None:
        self.courants: list[SearchResult] = []

    async def retrieve(self, query, k=5, score_threshold=0.3, store=None,
                       bm25_store=None, query_embedding=None):
        return list(self.courants)

    async def retrieve_many(self, queries, **kwargs):
        return {q: list(self.courants) for q in queries}


def _contexte() -> WorkspaceContext:
    espace = WorkspaceConfig(
        workspace_id="reference-marches-publics",
        name="Rédaction de marchés publics",
        storage_path="/colaig-reference-marches-publics/",
        description=("Assistance à la rédaction de marchés publics. Corpus : Code de la "
                     "commande publique, version consolidée, articles en vigueur "
                     "uniquement — 1021 articles répartis en 108 documents."),
        language="fr",
    )
    ctx = WorkspaceContext(workspace=espace, mode=ContextMode.ASSISTANT)
    ctx.system_prompt = _GEN["prompt_systeme"]()
    return ctx


class _RetrieverVif:
    """Un retriever qui cherche VRAIMENT, avec la requete recue et le k demande.

    POURQUOI IL FALLAIT LE FAIRE.

    `_RetrieverFige` rend toujours la meme liste, quelles que soient la requete et
    la profondeur. Trois consequences, toutes invisibles jusqu'au 03/09/2026 :

    - la reformulation de l'Analyseur n'atteint rien ;
    - les `chunk_queries` — « 2-3 reformulations variees » que son prompt lui demande
      de produire — ne servent a rien ;
    - le `k` de production (`workspace.max_results`, 5) n'est jamais applique, la
      mesure en servant 10.

    Et l'Orchestrateur, prive de LLM, tombait en mode deterministe : `0 ms` a chaque
    appel. Un tiers du pipeline echappait donc a toute mesure, et aucune des
    conclusions tirees sur « le pipeline » ne le concernait.

    Le mode fige reste le DEFAUT : il repond a une autre question — « le pipeline
    redige-t-il mieux que le coeur, a matiere egale ? » — et toutes les campagnes
    deja menees s'y comparent. Les confondre serait remplacer une mesure par une
    autre sans le dire.
    """

    def __init__(self, store, embed, cle: str) -> None:
        self._store, self._embed, self._cle = store, embed, cle

    async def retrieve(self, query, k=5, score_threshold=0.3, store=None,
                       bm25_store=None, query_embedding=None):
        vecteur = query_embedding or self._embed([query], self._cle)[0]
        trouves = (store or self._store).search(vecteur, k=k)
        return [r for r in trouves if r.score >= score_threshold]

    async def retrieve_many(self, queries, **kwargs):
        return {q: await self.retrieve(q, **kwargs) for q in queries}


_RETRIEVER = _RetrieverFige()
# TROIS MONTAGES, parce qu'ils repondent a trois questions differentes.
#
#   figee        passages imposes, Orchestrateur inerte — « le pipeline redige-t-il
#                mieux que le coeur, a matiere egale ? » C'est le DEFAUT, et toutes
#                les campagnes anterieures s'y comparent.
#   deterministe recherche vive, mais sans boucle agentique — isole ce que coute et
#                ce que rapporte la recherche elle-meme.
#   vive         recherche vive + LLM + outils — le pipeline tel qu'il tourne.
#
# Mesure du 03/09/2026 en mode vif : l'orchestration consomme 6300 ms sur 12 100,
# soit la MOITIE du temps, et ramene toujours les memes passages que la recherche
# directe (« jeux de passages distincts = 1 » sur tous les cas mesures). Le mode
# deterministe dit si ses etapes et ses reformulations servent a quelque chose.
_ORCHESTRATION = os.environ.get("COLAIG_REF_ORCHESTRATION", "figee").lower()
_ORCHESTRATION_VIVE = _ORCHESTRATION in ("vive", "vif", "1", "true")
_RECHERCHE_VIVE = _ORCHESTRATION_VIVE or _ORCHESTRATION.startswith("determinis")
_ETAT: dict = {}


def _agents(cle_api: str):
    if "analyseur" not in _ETAT:
        llm = _LLMPipeline(cle_api)
        _ETAT["llm"] = llm._interne
        # L'ANALYSEUR SUIT LE REGLAGE DE L'INSTANCE, comme le Synthetiseur suit sa
        # temperature. Mesure du 02/09/2026 : il consomme 3843 ms sur les 6.4 s du
        # pipeline — 62 % du temps — et produit deux intentions differentes pour la
        # meme question, ce qui en fait le premier suspect de l'inconstance.
        #
        # `use_tool_calling` lui fait rendre son Intent par un appel d'outil, donc en
        # JSON garanti, au lieu de le rediger en texte libre et de le parser. C'est la
        # piste a mesurer : structure garantie contre parsing, et son effet sur la
        # variance comme sur la latence.
        _ETAT["analyseur"] = Analyser(
            albert=llm, storage=FakeStorage(),
            use_tool_calling=os.environ.get(
                "COLAIG_ANALYSER_USE_TOOL_CALLING", "").lower() in ("1", "true", "oui"))
        # L'ORCHESTRATEUR, ENTIER OU INERTE — et il faut savoir lequel on mesure.
        #
        # Construit ainsi — sans `albert`, sans `tool_registry` — il tombe en mode
        # deterministe : sa boucle agentique ne tourne pas, et il consomme 0 ms. En
        # production il recoit les deux, fait quatre etapes et met 3243 ms.
        #
        # Le mode vif lui rend son LLM, son registre d'outils et un retriever qui
        # cherche vraiment. C'est le seul montage ou les TROIS agents travaillent.
        if _RECHERCHE_VIVE:
            from colaig.agents import build_tool_registry

            chunks = _GEN["decouper"](_GEN["PERIMETRE"])
            store = _GEN["FaissStore"](dimension=_GEN["_ns"]["DIMENSION"])
            store.add(_GEN["embed"]([c.text for c in chunks], cle_api), chunks)
            _ETAT["retriever"] = _RetrieverVif(store, _GEN["embed"], cle_api)
            if _ORCHESTRATION_VIVE:
                _ETAT["registre"] = build_tool_registry(
                    _ETAT["retriever"], FakeStorage(), llm)
                _ETAT["orchestrateur"] = Orchestrator(
                    FakeStorage(), _ETAT["retriever"], albert=llm,
                    tool_registry=_ETAT["registre"],
                    max_iterations=int(os.environ.get(
                        "COLAIG_ORCHESTRATOR_MAX_ITERATIONS", "5")),
                    temperature=float(os.environ.get(
                        "COLAIG_ORCHESTRATOR_TEMPERATURE", "0.1")))
            else:
                # Recherche vive, SANS LLM : l'Orchestrateur retombe en mode
                # deterministe. Ce qu'on retire ici est exactement sa boucle.
                _ETAT["orchestrateur"] = Orchestrator(FakeStorage(), _ETAT["retriever"])
        else:
            _ETAT["orchestrateur"] = Orchestrator(FakeStorage(), _RETRIEVER)
        # LA TEMPERATURE DOIT ETRE CELLE DE LA PRODUCTION, pas le defaut de la classe.
        #
        # Construit sans argument, le Synthetiseur prend 0.3 — alors que la reference
        # genere a 0.1. Les deux mesures n'etaient donc pas comparables, et une part de
        # l'ecart observe sur le refus etait celle de leurs reglages : a 0.3 le pipeline
        # refusait deux essais sur trois puis repondait au troisieme.
        #
        # Meme variable et meme defaut que `config.py`, pour que le harnais suive
        # l'instance au lieu d'avoir sa propre idee.
        _ETAT["synthetiseur"] = Synthesiser(
            albert=llm, storage=FakeStorage(),
            temperature=float(os.environ.get("COLAIG_SYNTHESISER_TEMPERATURE", "0.3")))
    return _ETAT["analyseur"], _ETAT["orchestrateur"], _ETAT["synthetiseur"]



def _directives_de(intent, cible: str) -> dict:
    """Les consignes que l'Analyseur adresse a un agent, sous forme comparable.

    Deux essais dont les directives different donnent au Synthetiseur deux consignes
    differentes : c'est la seule voie par laquelle l'Analyseur peut faire basculer un
    refus, le retriever etant fige.
    """
    d = getattr(intent, f"{cible}_directives", None)
    if d is None:
        return {}
    if isinstance(d, dict):        # certaines variantes rendent le JSON brut
        return {k: d[k] for k in sorted(d)}
    return {
        "instructions": (getattr(d, "instructions", "") or "").strip(),
        "format": getattr(d, "response_format", "") or "",
        "ton": getattr(d, "response_tone", "") or "",
        "focus": sorted(getattr(d, "focus_points", []) or []),
        "strategie": getattr(d, "search_strategy", "") or "",
    }


async def _repondre_par_le_pipeline(question: str, trouves, cle_api: str):
    analyseur, orchestrateur, synthetiseur = _agents(cle_api)
    # En mode fige, on impose les passages de la reference ; en mode vif,
    # l'Orchestrateur va les chercher lui-meme et cette ligne n'a pas de sens.
    if not _RECHERCHE_VIVE:
        _RETRIEVER.courants = list(trouves)
    contexte = _contexte()

    message = IncomingMessage(user_id="@mesure:tchap.gouv.fr",
                              conversation_id="!mesure:tchap.gouv.fr",
                              body=question)

    debut = time.monotonic()
    intent = await analyseur.analyse(message, contexte)
    t_analyse = time.monotonic()

    # BRAS EXPERIMENTAL — le Synthetiseur sans les directives de l'Analyseur.
    #
    # Mesure du 03/09/2026 : pour une meme question et des passages identiques,
    # l'Analyseur produit jusqu'a SIX jeux de directives distincts sur six essais. Le
    # `format` bascule entre « list » et « paragraph », et les points de focus changent
    # de sujet — d'une liste de services a une question de seuils. Le Synthetiseur
    # recoit donc des consignes differentes a chaque appel : c'est la seule voie par
    # laquelle un refus peut basculer, les passages etant figes.
    #
    # Ce bras coupe cette voie pour verifier qu'elle est bien la cause. Il ne prejuge
    # pas du correctif : contraindre les directives et les supprimer sont deux options,
    # et c'est la mesure qui doit departager.
    if os.environ.get("COLAIG_REF_SANS_DIRECTIVES", "").lower() in ("1", "true", "oui"):
        intent.synthesiser_directives = None
    plan = await orchestrateur.execute(intent, contexte)
    t_orchestre = time.monotonic()
    reponse = await synthetiseur.synthesise(plan, contexte, [], None, message=message)
    duree = time.monotonic() - debut

    texte = (reponse.text or "")

    # LA TRACE PAR AGENT — de quoi attribuer la variance a son auteur.
    #
    # Trois tirages du meme montage donnent 18, 19 puis 16 sur 20 (02/09/2026), la ou
    # le coeur rend 20, 20, 20. Le pipeline varie donc de trois cas, mais un chiffre
    # global ne dit pas OU : l'Analyseur peut reformuler autrement, l'Orchestrateur
    # retenir d'autres passages, le Synthetiseur rediger differemment.
    #
    # Ces trois champs permettent le diagnostic par elimination, en comparant deux
    # tirages du MEME cas :
    #   intent identique + passages identiques + reponse differente -> Synthetiseur
    #   intent identique + passages differents                      -> Orchestrateur
    #   intent different                                            -> Analyseur
    #
    # `passages` porte les SECTIONS et non le texte : c'est ce qui identifie un passage
    # sans gonfler la trace, et cela revele au passage les doublons que l'Orchestrateur
    # accumule d'une etape a l'autre.
    sections = [getattr(r.chunk, "section", "") or "" for r in plan.search_results]
    return texte, duree, {
        "needs_rag": intent.needs_rag,
        "intent": intent.intent_type.value,
        "sources": len(plan.search_results),
        # Analyseur — la reformulation ET les directives, car les deux l'atteignent.
        #
        # Les directives manquaient a la premiere version de cette trace, et c'est ce
        # qui rendait l'attribution fausse : sur le cas le plus instable, le type et
        # `needs_rag` sont TOUJOURS identiques, seule la forme de la reformulation
        # bouge — « invoquables » / « invocables ». Le retriever etant fige, cette
        # variation-la ne touche pas les passages. Ce qui atteint le Synthetiseur, et
        # peut donc faire basculer un refus, ce sont les CONSIGNES que l'Analyseur lui
        # adresse : instructions, format, points de focus.
        "reformulation": (intent.query_reformulated or "").strip(),
        "entites": sorted(getattr(intent, "entities", []) or []),
        "directives_synthese": _directives_de(intent, "synthesiser"),
        "directives_orchestre": _directives_de(intent, "orchestrator"),
        # Orchestrateur
        "passages": sections,
        "passages_distincts": len(set(sections)),
        "etapes": len(getattr(plan, "steps", []) or []),
        # Synthetiseur
        "reponse_caracteres": len(texte),
        # Ou passe le temps
        "ms_analyse": int((t_analyse - debut) * 1000),
        "ms_orchestration": int((t_orchestre - t_analyse) * 1000),
        "ms_synthese": int((duree - (t_orchestre - debut)) * 1000),
    }


_OBSERVATIONS: list[dict] = []


def repondre(systeme, question, trouves, cle_s):  # noqa: ARG001
    """La substitution unique. Même signature que celle de la référence.

    `systeme` est ignoré : le prompt système est porté par le WorkspaceContext, comme
    en production. Le passer deux fois le dupliquerait dans l'appel.
    """
    texte, duree, trace = asyncio.run(
        _repondre_par_le_pipeline(question, trouves, cle_s))
    trace["question"] = question
    _OBSERVATIONS.append(trace)
    # `tronquee` : la MEME regle que la reference, et non plus une devinette.
    #
    # Ce commentaire disait deja « meme regle », mais la ligne suivante devinait a la
    # ponctuation finale, la ou la reference lit `finish_reason == "length"`. Deux
    # regles differentes ecartent des observations differentes, donc ne comparent pas
    # les memes denominateurs.
    tronquee = getattr(_ETAT.get("llm"), "dernier_finish_reason", "") == "length"
    return texte, duree, tronquee


if __name__ == "__main__":
    # LE NOM DU RAPPORT DOIT PORTER LE PIPELINE.
    #
    # Sans cette marque, ce harnais ecrit sous le meme nom que la mesure du coeur —
    # et l'ecrase. C'est arrive a la premiere execution, le 30/08/2026 : TROISIEME
    # occurrence du meme piege dans la journee, apres le k et le modele d embedding,
    # et la premiere que je commets moi-meme apres l'avoir corrigee deux fois.
    #
    # `MARQUE` existe pour cela : un champ libre, ajoute au nom, qui dit ce que la
    # mesure a d unique quand tous les autres champs coincident.
    # LA MARQUE DE L'APPELANT PRIME, et c'est ce qui permet plusieurs tirages.
    #
    # Cette ligne ecrasait `COLAIG_REF_MARQUE`. Trois tirages lances le 02/09/2026 avec
    # les marques « pipeline-t1/t2/t3 » ont donc tous ecrit sous le meme nom, et se sont
    # ecrases : deux campagnes perdues, sans un mot. QUATRIEME occurrence de ce piege
    # dans le chantier — apres le k, le modele d'embedding et le mode negatifs-seuls.
    #
    # « pipeline » reste le defaut, pour que ce harnais n'ecrase jamais la mesure du
    # coeur ; mais qui demande une marque l'obtient.
    _GEN["MARQUE"] = os.environ.get("COLAIG_REF_MARQUE") or "pipeline"
    _GEN["repondre"] = repondre
    code = _GEN["main"]()

    if _OBSERVATIONS:
        # LES TRACES SONT ARCHIVEES, comme les reponses le sont deja : attribuer la
        # variance a un agent exige de comparer deux tirages, donc de les conserver.
        import json as _json
        import time as _time
        _marque = _GEN["MARQUE"]
        _traces = (RACINE / "_chantier" / "mesures"
                   / f"traces-{_marque}-{_time.strftime('%Y%m%d')}.json")
        _traces.write_text(_json.dumps(_OBSERVATIONS, ensure_ascii=False, indent=1),
                           encoding="utf-8")
        print(f"traces par agent : {_traces.name}")

        ferme = sum(1 for o in _OBSERVATIONS if not o["needs_rag"])
        sans_source = sum(1 for o in _OBSERVATIONS if o["sources"] == 0)
        print()
        print(f"pipeline — {len(_OBSERVATIONS)} passages")
        print(f"  needs_rag=False   : {ferme}  ({ferme / len(_OBSERVATIONS):.1%})")
        print(f"  plan sans source  : {sans_source}  "
              f"({sans_source / len(_OBSERVATIONS):.1%})")
    raise SystemExit(code)
