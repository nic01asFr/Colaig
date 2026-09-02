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
        """Sans outils : le jeu doré est documentaire, aucun outil n'a de sens ici.

        Les rendre disponibles mesurerait la boucle d'outils, pas le pipeline — et
        aucun cas doré n'attend une action.
        """
        from colaig.models import ChatCompletionResult

        texte = await self.chat(messages, model=model, temperature=temperature,
                                max_tokens=max_tokens, priority=priority)
        return ChatCompletionResult(content=texte, tool_calls=[])


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


_RETRIEVER = _RetrieverFige()
_ETAT: dict = {}


def _agents(cle_api: str):
    if "analyseur" not in _ETAT:
        llm = _LLMPipeline(cle_api)
        _ETAT["llm"] = llm._interne
        _ETAT["analyseur"] = Analyser(albert=llm, storage=FakeStorage())
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


async def _repondre_par_le_pipeline(question: str, trouves, cle_api: str):
    analyseur, orchestrateur, synthetiseur = _agents(cle_api)
    _RETRIEVER.courants = list(trouves)
    contexte = _contexte()

    message = IncomingMessage(user_id="@mesure:tchap.gouv.fr",
                              conversation_id="!mesure:tchap.gouv.fr",
                              body=question)

    debut = time.monotonic()
    intent = await analyseur.analyse(message, contexte)
    t_analyse = time.monotonic()
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
        # Analyseur
        "reformulation": (intent.query_reformulated or "").strip(),
        "entites": sorted(getattr(intent, "entities", []) or []),
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
