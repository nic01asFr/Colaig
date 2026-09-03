"""
Colaig — le chart doit pouvoir decrire l'instance qui tourne.

CE QUI A ETE CONSTATE LE 30/08/2026
-------------------------------------
La release `colaig-test` porte des valeurs Helm qui **ne decrivent pas** ce qui
tourne. Rendu du chart avec les valeurs enregistrees de la release elle-meme :

    STORAGE_BACKEND: "local"        <- l'instance tourne sur s3 (bucket MinIO)

et ni `LLM_MODEL_OCR`, ni `COLAIG_EMBEDDING_DIM`, ni `COLAIG_AUTO_DISCOVER_*` dans le
rendu, alors que les trois sont poses sur le deploiement.

La configuration a ete appliquee par `kubectl set env`, jamais reportee dans la
release. Elle n'existe donc **nulle part hors du cluster** : recreer la release la
perd, et un `helm upgrade --reuse-values` rebasculerait le stockage sur `local` en
eteignant l'OCR au passage.

Ce n'est pas un probleme d'exploitation, c'est un probleme de reproductibilite : une
instance dont la configuration n'est ecrite nulle part ne peut pas etre remontee, ni
comparee, ni auditee.

LE PIEGE DANS LE CHART LUI-MEME
--------------------------------
`S3_ACCESS_KEY` etait rendu des que le backend valait `s3`, meme vide. Or les
identifiants arrivent par `envFrom: secretRef`, et **dans Kubernetes une entree `env`
prime sur `envFrom`**. Passer le backend a `s3` sans renseigner la cle dans les
valeurs aurait donc pose `S3_ACCESS_KEY: ""` et **ecrase la cle du secret** — l'acces
au stockage tombait, sans que rien dans les valeurs ne paraisse fautif.

Le meme raisonnement vaut pour toute variable que le secret peut fournir : le chart
ne doit ecrire que ce qu'on lui a effectivement donne.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

CHART = Path("deploy/helm/colaig")
VALEURS_INSTANCE = CHART / "values-colaig-test.yaml"

# Ce que le deploiement en service porte reellement, releve le 30/08/2026 par
# `kubectl get deploy colaig-test-colaig -o json`. Hors variables issues du secret.
ENV_DE_L_INSTANCE = {
    "STORAGE_BACKEND": "s3",
    "MESSAGING_BACKEND": "matrix",
    "LLM_BACKEND": "openai",
    "LLM_API_URL": "https://llm.lab.sspcloud.fr/api",
    "ALBERT_API_URL": "https://llm.lab.sspcloud.fr/api",
    "COLAIG_LOCAL_EMBEDDINGS": "false",
    "LLM_MODEL_CHAT": "qwen3-6-35b-moe",
    "ALBERT_MODEL_CHAT": "qwen3-6-35b-moe",
    "LLM_MODEL_EMBED": "qwen3-embedding-8b",
    "ALBERT_MODEL_EMBED": "qwen3-embedding-8b",
    "LLM_MODEL_OCR": "chandra-ocr-2",
    "COLAIG_EMBEDDING_DIM": "4096",
    # Le pipeline agent, active le 01/09/2026. Cette entree est ce qui force le
    # deploiement a suivre le chart : tant que l'instance ne la porte pas, le test
    # echoue et dit que les deux ont diverge.
    "COLAIG_AGENTS_ENABLED": "1",
    # Alignee sur la temperature de la reference : sans quoi les deux
    # systemes ne sont pas comparables, et l'ecart mesure est en partie
    # celui de leurs reglages.
    "COLAIG_SYNTHESISER_TEMPERATURE": "0.1",
    # Poses explicitement : sans eux, le Synthetiseur et la
    # contextualisation convergent avec les autres agents par repli, ce qui
    # ne se voit nulle part et se defait au premier reglage pose a la main.
    "ALBERT_MODEL_MEDIUM": "qwen3-6-35b-moe",
    "ALBERT_MODEL_LIGHT": "qwen3-6-35b-moe",
    "COLAIG_AUTO_DISCOVER_ENABLED": "true",
    "COLAIG_AUTO_DISCOVER_INTERVAL": "120",
    "S3_BUCKET_NAME": "colaig",
    "S3_ENDPOINT_URL": "https://minio.lab.sspcloud.fr",
    "S3_REGION": "us-east-1",
    "COLAIG_WEB_PORT": "8000",
    "COLAIG_DATA_DIR": "/app/data",
    "COLAIG_LOCAL_HOME": "/app/data/.colaig",
    "MATRIX_HOMESERVER": "https://matrix.agent.dev-durable.tchap.gouv.fr",
    "MATRIX_USERNAME": "@colaig.assistant-developpement-durable.gouv.fr"
                       ":agent.dev-durable.tchap.gouv.fr",
}

helm = shutil.which("helm")
besoin_de_helm = pytest.mark.skipif(
    helm is None, reason="helm absent — le rendu du chart ne peut pas etre verifie")


def _rendu(*valeurs: Path) -> str:
    cmd = [helm, "template", "colaig-test", str(CHART)]
    for v in valeurs:
        cmd += ["-f", str(v)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"helm template a echoue :\n{r.stderr}"
    return r.stdout


def _env(rendu: str) -> dict[str, str]:
    """Les paires name/value de la section `env` du conteneur."""
    paires = re.findall(r"- name: ([A-Z0-9_]+)\n\s+value: \"?([^\"\n]*)\"?", rendu)
    return dict(paires)


# ─────────────────────────────────────────────────────────────────────────────
# Le piege : une valeur vide ecrase le secret
# ─────────────────────────────────────────────────────────────────────────────


@besoin_de_helm
def test_une_cle_non_renseignee_n_ecrase_pas_le_secret(tmp_path):
    """LE defaut. `env` prime sur `envFrom` : rendre une chaine vide efface la cle."""
    v = tmp_path / "s3-sans-cle.yaml"
    v.write_text("storage:\n  backend: s3\n  s3:\n    bucket: b\n"
                 "    endpointUrl: https://exemple.invalid\n", encoding="utf-8")

    env = _env(_rendu(v))

    assert env.get("S3_BUCKET_NAME") == "b", "le bucket doit bien etre pose"
    assert "S3_ACCESS_KEY" not in env, (
        "S3_ACCESS_KEY est rendu vide alors qu'aucune cle n'a ete fournie : il "
        "ecrasera celle du secret et l'acces au stockage tombera"
    )


@besoin_de_helm
def test_une_cle_renseignee_est_bien_posee(tmp_path):
    """Le pendant : le chart doit rester utilisable sans secret externe."""
    v = tmp_path / "s3-avec-cle.yaml"
    v.write_text("storage:\n  backend: s3\n  s3:\n    bucket: b\n"
                 "    accessKey: UNE-CLE\n", encoding="utf-8")

    assert _env(_rendu(v)).get("S3_ACCESS_KEY") == "UNE-CLE"


# ─────────────────────────────────────────────────────────────────────────────
# Le chart doit savoir exprimer ce que l'instance porte
# ─────────────────────────────────────────────────────────────────────────────


def test_le_fichier_de_valeurs_de_l_instance_existe():
    """Sans lui, la configuration de l'instance n'existe que dans le cluster."""
    assert VALEURS_INSTANCE.exists(), (
        f"{VALEURS_INSTANCE} manque : l'instance ne peut pas etre remontee a "
        "l'identique, ni comparee, ni auditee"
    )


@besoin_de_helm
def test_le_rendu_reproduit_l_instance_en_service():
    """LA propriete du lot. Ce que le chart rend doit etre ce qui tourne.

    Si ce test passe, `helm upgrade -f values-colaig-test.yaml` devient inoffensif :
    il reecrit ce qui est deja la. C'est exactement ce qui manquait.
    """
    env = _env(_rendu(VALEURS_INSTANCE))

    manquants = {k: v for k, v in ENV_DE_L_INSTANCE.items() if k not in env}
    assert not manquants, f"le chart ne rend pas : {sorted(manquants)}"

    differents = {k: (v, env[k]) for k, v in ENV_DE_L_INSTANCE.items() if env[k] != v}
    assert not differents, f"valeurs divergentes (attendu, rendu) : {differents}"


@besoin_de_helm
def test_le_volume_et_la_strategie_suivent():
    """Les deux correctifs du 30/08 doivent etre dans le fichier de l'instance."""
    rendu = _rendu(VALEURS_INSTANCE)

    assert "persistentVolumeClaim" in rendu, (
        "sans volume persistant, l'identite Matrix meurt a chaque redemarrage"
    )
    assert "type: Recreate" in rendu, (
        "sans Recreate, deux pods partagent le magasin de cles pendant un deploiement"
    )


@besoin_de_helm
def test_aucun_secret_dans_le_fichier_de_valeurs():
    """Le depot est public. Un secret commite ne se rattrape pas."""
    texte = VALEURS_INSTANCE.read_text(encoding="utf-8")

    for champ in ("accessKey", "secretKey", "sessionToken", "apiKey", "platformApiKey"):
        for ligne in texte.splitlines():
            if ligne.strip().startswith(f"{champ}:"):
                valeur = ligne.split(":", 1)[1].strip().strip("\"'")
                assert not valeur, f"« {champ} » porte une valeur : {ligne.strip()}"


# Variables que le rendu peut ajouter sans changer le comportement : des alias que le
# chart pose systematiquement a cote de leur equivalent `LLM_*`.
ALIAS_INOFFENSIFS = {"ALBERT_MODEL_OCR", "ALBERT_MODEL_CHAT", "ALBERT_MODEL_EMBED"}


@besoin_de_helm
def test_le_rendu_n_ajoute_rien_qui_changerait_le_comportement():
    """L'autre moitie de la propriete, et elle a servi tout de suite.

    Verifier que rien ne MANQUE ne suffit pas : ce qui s'AJOUTE peut casser autant.
    Au premier rendu, le chart posait `S3_PREFIX: colaig` — son defaut — alors que le
    deploiement en service ne pose pas cette variable.

    `_full_key()` prefixe TOUS les chemins : `/colaig-mesure-sst/...` serait devenu
    `colaig/colaig-mesure-sst/...`. Colaig aurait vu un espace vide, sans erreur, et
    reindexe le neant par-dessus soixante documents.

    Un `helm upgrade` l'aurait pose. C'est la comparaison qui l'a attrape, pas le
    raisonnement — je croyais le fichier de valeurs complet.
    """
    rendu = set(_env(_rendu(VALEURS_INSTANCE)))

    ajouts = rendu - set(ENV_DE_L_INSTANCE) - ALIAS_INOFFENSIFS
    assert not ajouts, (
        f"le rendu pose des variables absentes du deploiement en service : "
        f"{sorted(ajouts)} — chacune peut changer le comportement en silence"
    )
