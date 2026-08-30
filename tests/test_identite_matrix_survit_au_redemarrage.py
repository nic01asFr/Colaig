"""
Colaig — l'identite Matrix doit survivre a un redemarrage.

CE QUE LES JOURNAUX DE PRODUCTION DISENT, LE 30/08/2026
--------------------------------------------------------
Environ 450 avertissements, repartis sur une douzaine de salons :

    message non dechiffre dans !HSwgmpTDgVFXUwecab:agent.dev-durable.tchap.gouv.fr
      (expediteur @colaig.assistant-...)

**L'expediteur est Colaig lui-meme.** Il ne sait pas relire ce qu'il a ecrit.

LA CAUSE, ET POURQUOI ELLE ETAIT INVISIBLE
--------------------------------------------
Le magasin de chiffrement (`e2e_store`) et le jeton de session vivent sous
`paths.local_home_dir()`, soit `~/.colaig/` — donc `/root/.colaig/` dans l'image.

Le deploiement monte un volume, mais sur `/app/data`. Le chart le dit lui-meme :

    # Volume de cache local ephemere (FAISS reconstruit au restart)

Ce commentaire est **juste pour ce qu'il decrit** : l'index FAISS se reconstruit. Mais
`/root/.colaig/` n'est couvert par **aucun** volume, et lui ne se reconstruit pas. On
peut donc activer `persistence` sans rien regler — c'est ce que j'ai d'abord cru faire.

CE QUE COUTE UN REDEMARRAGE, AUJOURD'HUI
------------------------------------------
- une **nouvelle identite d'appareil**, donc un appareil non verifie de plus dans la
  liste de chaque membre du salon, a chaque redemarrage ;
- **toutes les cles Megolm perdues** : l'historique chiffre devient illisible, y
  compris les propres reponses de Colaig ;
- la memoire conversationnelle des salons chiffres s'arrete au dernier redemarrage.

LA PROPRIETE FIGEE ICI
------------------------
Le dossier local de Colaig doit pouvoir etre **place ou l'exploitant le decide**, donc
dans un volume persistant. `local_home_dir()` honore `COLAIG_LOCAL_HOME` ; sans lui,
le comportement d'avant est conserve a l'identique.
"""

from __future__ import annotations

from pathlib import Path

from colaig import paths


def test_le_defaut_ne_bouge_pas(monkeypatch):
    """Sans la variable, le comportement historique — aucune migration imposee."""
    monkeypatch.delenv("COLAIG_LOCAL_HOME", raising=False)

    assert paths.local_home_dir() == Path.home() / ".colaig"


def test_l_exploitant_peut_placer_le_dossier_dans_un_volume(monkeypatch, tmp_path):
    """Ce qui permet au magasin de cles de survivre au redemarrage."""
    monkeypatch.setenv("COLAIG_LOCAL_HOME", str(tmp_path / "persistant"))

    assert paths.local_home_dir() == tmp_path / "persistant"


def test_le_jeton_et_le_magasin_suivent(monkeypatch, tmp_path):
    """LE point. Ces deux-la sont ce qu'un redemarrage detruisait."""
    monkeypatch.setenv("COLAIG_LOCAL_HOME", str(tmp_path / "persistant"))

    jeton = paths.local_file("matrix_token.json")

    assert jeton == tmp_path / "persistant" / "matrix_token.json"
    # Le magasin E2E est le frere du jeton — c'est ainsi que matrix.py le derive.
    assert (jeton.parent / "e2e_store").parent == tmp_path / "persistant"


def test_un_chemin_vide_vaut_absence(monkeypatch):
    """Une variable posee mais vide est une erreur de deploiement courante.

    La traiter comme un chemin ferait ecrire le magasin a la racine du conteneur.
    """
    monkeypatch.setenv("COLAIG_LOCAL_HOME", "")

    assert paths.local_home_dir() == Path.home() / ".colaig"


# ─────────────────────────────────────────────────────────────────────────────
# Le lien entre le code et le deploiement — c'est lui qui avait cede
# ─────────────────────────────────────────────────────────────────────────────


def test_le_chart_place_le_dossier_local_dans_le_volume_monte():
    """Le defaut n'etait ni dans le code ni dans le chart, mais dans leur JOINTURE.

    Le code ecrivait sous `~/.colaig/`, le chart montait `/app/data`. Chacun etait
    coherent avec lui-meme. Ce test refuse qu'ils redivergent.
    """
    import re

    deploiement = Path("deploy/helm/colaig/templates/deployment.yaml").read_text(
        encoding="utf-8")

    montage = re.search(r"mountPath:\s*(\S+)", deploiement)
    assert montage, "aucun volume monte dans le deploiement"
    chemin_monte = montage.group(1)

    assert "COLAIG_LOCAL_HOME" in deploiement, (
        "le chart ne place pas le dossier local de Colaig : le magasin de cles Matrix "
        "retombera sur ~/.colaig/, hors de tout volume, et sera perdu au redemarrage"
    )

    valeur = re.search(r"COLAIG_LOCAL_HOME\"?\s*\n\s*value:\s*\"?([^\"\n]+)", deploiement)
    assert valeur, "COLAIG_LOCAL_HOME est nomme mais sans valeur"
    assert valeur.group(1).startswith(chemin_monte), (
        f"COLAIG_LOCAL_HOME vaut « {valeur.group(1)} », hors du volume monte "
        f"« {chemin_monte} » — le magasin de cles ne survivrait pas au redemarrage"
    )
