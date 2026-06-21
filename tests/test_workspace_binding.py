"""
Tests du sélecteur d'espace `.colaig` pour un salon (auto-binding à l'invitation).
Fonctions pures — aucune I/O.
"""
from app.agent.workspace_binding import score_candidate, select_workspace


def _cand(name, path, descriptor=None):
    return {"name": name, "path": path, "descriptor": descriptor or {}}


def test_conversation_explicite_gagne():
    # Champ réel `conversations` (room déjà rattaché) — signal le plus fort.
    cands = [
        _cand("Urbanisme", "Urbanisme", {"conversations": ["!abc:s"]}),
        _cand("RH", "RH", {"match": {"room_name": "(?i).*"}}),  # matcherait tout
    ]
    best = select_workspace(cands, room_id="!abc:s", room_name="Coucou", room_topic="")
    assert best is not None
    assert best["path"] == "Urbanisme"
    assert best["reason"] == "conversation"


def test_user_id_dm():
    # Workspace personnel : match par user_ids (mode DM).
    cands = [_cand("Perso", "perso", {"user_ids": ["@nicolas:s"]})]
    best = select_workspace(cands, room_id="!dm:s", user_id="@nicolas:s")
    assert best and best["reason"] == "user_id"


def test_regex_nom_salon():
    cands = [_cand("Urbanisme", "Urbanisme", {"match": {"room_name": "(?i)urbanism"}})]
    best = select_workspace(cands, room_id="!x:s", room_name="Salon Urbanisme — Mairie")
    assert best and best["path"] == "Urbanisme" and best["reason"] == "room_name"


def test_regex_topic():
    cands = [_cand("PLU", "PLU", {"match": {"room_topic": r"(?i)\bPLU\b"}})]
    best = select_workspace(cands, room_id="!x:s", room_name="Divers", room_topic="Questions PLU 2026")
    assert best and best["reason"] == "room_topic"


def test_convention_de_nom():
    cands = [_cand("Urbanisme", "dossiers/Urbanisme")]
    best = select_workspace(cands, room_id="!x:s", room_name="urbanisme")
    assert best and best["reason"] == "name_convention"


def test_convention_accents_insensible():
    cands = [_cand("Préfecture", "Prefecture")]
    best = select_workspace(cands, room_id="!x:s", room_name="PREFECTURE")
    assert best and best["reason"] == "name_convention"


def test_repli_default_workspace():
    cands = [_cand("Archivist", "Archivist"), _cand("Autre", "Autre")]
    best = select_workspace(cands, room_id="!x:s", room_name="Sans rapport", default_workspace="Archivist")
    assert best and best["path"] == "Archivist" and best["reason"] == "default_workspace"


def test_aucune_correspondance():
    cands = [_cand("A", "A"), _cand("B", "B")]
    assert select_workspace(cands, room_id="!x:s", room_name="Zzz") is None


def test_priority_departage():
    cands = [
        _cand("A", "A", {"match": {"room_name": "(?i)test"}, "priority": 1}),
        _cand("B", "B", {"match": {"room_name": "(?i)test"}, "priority": 50}),
    ]
    best = select_workspace(cands, room_id="!x:s", room_name="un test")
    assert best["path"] == "B"


def test_regex_invalide_tolere():
    # Pattern invalide → pas de crash, pas de match
    assert score_candidate(
        descriptor={"match": {"room_name": "[invalid("}},
        folder_name="X", candidate_path="X",
        room_id="!x:s", room_name="abc", room_topic="",
    ) == 0
