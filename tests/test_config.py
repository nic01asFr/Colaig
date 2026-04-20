"""Tests pour colaig/config.py — chargement de configuration."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from colaig.config import load_config, _load_yaml
from colaig.models import ColaigConfig


class TestLoadYaml:
    """Tests du chargement YAML."""

    def test_load_default_yml(self, tmp_path: Path):
        """Charge un fichier YAML valide."""
        yml = tmp_path / "config.yml"
        yml.write_text("rag:\n  chunk_size: 500\n", encoding="utf-8")
        result = _load_yaml(yml)
        assert result["rag"]["chunk_size"] == 500

    def test_load_missing_file(self, tmp_path: Path):
        """Retourne dict vide si le fichier n'existe pas."""
        result = _load_yaml(tmp_path / "inexistant.yml")
        assert result == {}

    def test_load_empty_file(self, tmp_path: Path):
        """Retourne dict vide si le fichier est vide."""
        yml = tmp_path / "empty.yml"
        yml.write_text("", encoding="utf-8")
        result = _load_yaml(yml)
        assert result == {}


class TestLoadConfig:
    """Tests du chargement complet de la configuration."""

    def test_returns_colaig_config(self, tmp_path: Path):
        """load_config retourne une instance ColaigConfig."""
        yml = tmp_path / "config.yml"
        yml.write_text("{}", encoding="utf-8")
        config = load_config(yaml_path=yml)
        assert isinstance(config, ColaigConfig)

    def test_env_overrides_yaml(self, tmp_path: Path):
        """Les variables d'environnement écrasent les valeurs YAML."""
        yml = tmp_path / "config.yml"
        yml.write_text("rag:\n  chunk_size: 500\n", encoding="utf-8")

        env = {"COLAIG_CHUNK_SIZE": "999"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config(yaml_path=yml)
        assert config.chunk_size == 999

    def test_yaml_overrides_defaults(self, tmp_path: Path):
        """Les valeurs YAML écrasent les defaults de ColaigConfig."""
        yml = tmp_path / "config.yml"
        yml.write_text("rag:\n  chunk_size: 1200\n  max_results: 10\n", encoding="utf-8")

        # Nettoyer les variables d'env qui pourraient interférer
        env_clean = {
            k: "" for k in [
                "COLAIG_CHUNK_SIZE", "COLAIG_MAX_RESULTS",
            ] if k in os.environ
        }
        with patch.dict(os.environ, {}, clear=False):
            # S'assurer que les env ne sont pas définies
            for k in ["COLAIG_CHUNK_SIZE", "COLAIG_MAX_RESULTS"]:
                os.environ.pop(k, None)
            config = load_config(yaml_path=yml)

        assert config.chunk_size == 1200
        assert config.default_max_results == 10

    def test_defaults_when_no_env_no_yaml(self, tmp_path: Path):
        """Valeurs par défaut quand ni env ni yaml ne sont définis."""
        yml = tmp_path / "empty.yml"
        yml.write_text("{}", encoding="utf-8")

        # Patcher _load_env pour ne pas charger le vrai .env (qui contient des surcharges)
        with patch("colaig.config._load_env"):
            # Nettoyer toutes les env Colaig pour tester les vrais defaults
            env_keys = [
                "MATRIX_HOMESERVER", "MATRIX_USERNAME", "MATRIX_PASSWORD",
                "ALBERT_API_URL", "ALBERT_API_KEY",
                "WEBDAV_URL", "WEBDAV_USERNAME", "WEBDAV_PASSWORD",
                "COLAIG_LOG_LEVEL", "COLAIG_DATA_DIR", "COLAIG_WEB_PORT",
                "COLAIG_CHUNK_SIZE", "COLAIG_CHUNK_OVERLAP",
                "COLAIG_SIMILARITY_THRESHOLD", "COLAIG_MAX_RESULTS",
                "COLAIG_INDEX_REFRESH", "COLAIG_WORKSPACE_CACHE_TTL",
                "ALBERT_MODEL_CHAT", "ALBERT_MODEL_EMBED",
                "ALBERT_MODEL_RERANK", "ALBERT_MODEL_OCR", "ALBERT_MODEL_AUDIO",
            ]
            clean = {k: v for k, v in os.environ.items()}
            for k in env_keys:
                os.environ.pop(k, None)

            try:
                config = load_config(yaml_path=yml)
                assert config.albert_api_url == "https://albert-api.etalab.gouv.fr"
                assert config.chunk_size == 800
                assert config.chunk_overlap == 100
                assert config.log_level == "INFO"
                assert config.web_port == 8000
                assert config.albert_model_rerank == "BAAI/bge-reranker-v2-m3"
                assert config.albert_model_ocr == "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
                assert config.albert_model_audio == "openai/whisper-large-v3"
            finally:
                # Restaurer l'env
                os.environ.update(clean)

    def test_env_matrix_values(self, tmp_path: Path):
        """Les variables Matrix sont chargées depuis l'environnement."""
        yml = tmp_path / "config.yml"
        yml.write_text("{}", encoding="utf-8")

        env = {
            "MATRIX_HOMESERVER": "https://matrix.test.fr",
            "MATRIX_USERNAME": "@bot:test.fr",
            "MATRIX_PASSWORD": "secret123",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config(yaml_path=yml)

        assert config.matrix_homeserver == "https://matrix.test.fr"
        assert config.matrix_username == "@bot:test.fr"
        assert config.matrix_password == "secret123"

    def test_env_int_invalid_falls_back(self, tmp_path: Path):
        """Un entier invalide dans l'env tombe sur le default."""
        yml = tmp_path / "config.yml"
        yml.write_text("{}", encoding="utf-8")

        env = {"COLAIG_WEB_PORT": "not_a_number"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config(yaml_path=yml)
        assert config.web_port == 8000

    def test_env_float_invalid_falls_back(self, tmp_path: Path):
        """Un float invalide dans l'env tombe sur le default."""
        yml = tmp_path / "config.yml"
        yml.write_text("{}", encoding="utf-8")

        env = {"COLAIG_SIMILARITY_THRESHOLD": "invalid"}
        with patch.dict(os.environ, env, clear=False):
            config = load_config(yaml_path=yml)
        assert config.default_similarity_threshold == 0.3

    def test_with_real_default_yml(self):
        """Charge le vrai config/default.yml du projet."""
        project_yml = Path(__file__).resolve().parent.parent / "config" / "default.yml"
        if not project_yml.exists():
            pytest.skip("config/default.yml absent")

        # Nettoyer les env RAG pour laisser le YAML s'appliquer
        for k in ["COLAIG_CHUNK_SIZE", "COLAIG_CHUNK_OVERLAP",
                   "COLAIG_SIMILARITY_THRESHOLD", "COLAIG_MAX_RESULTS",
                   "COLAIG_INDEX_REFRESH", "COLAIG_WORKSPACE_CACHE_TTL"]:
            os.environ.pop(k, None)

        config = load_config(yaml_path=project_yml)
        # Valeurs définies dans config/default.yml
        assert config.chunk_size == 800
        assert config.chunk_overlap == 100
        assert config.default_similarity_threshold == 0.3
        assert config.default_max_results == 5
        assert config.index_refresh_interval_seconds == 300
        assert config.workspace_cache_ttl_seconds == 60
