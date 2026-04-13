"""Configuration management service for dynamic settings updates.

Handles runtime configuration changes including:
- Embedding model selection
- Chunking parameters
- Other indexing settings

Configuration is stored in SQLite database for persistence across restarts.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from config import Settings, settings
from services.app_database import app_db

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages dynamic configuration updates with database persistence."""

    def __init__(self, env_file: Path = Path(".env")):
        self.env_file = env_file

    def get_current_config(self) -> Dict[str, Any]:
        """Get current configuration values.

        Merges database overrides with .env defaults.
        Database values take precedence.
        """
        # Start with current settings from .env
        config = {
            "embedding_model": settings.embedding_model,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "default_top_k": settings.default_top_k,
            "max_top_k": settings.max_top_k,
            "data_dir": str(settings.data_dir),
            "host": settings.host,
            "port": settings.port,
            # OCR settings
            "enable_ocr": settings.enable_ocr,
            "ocr_max_pages": settings.ocr_max_pages,
            "ocr_max_file_mb": settings.ocr_max_file_mb,
            "vision_ocr_provider": settings.vision_ocr_provider,
            "vision_ocr_model": settings.vision_ocr_model,
            "vision_ocr_api_key": settings.vision_ocr_api_key,
            "vision_ocr_dpi": settings.vision_ocr_dpi,
            "vision_ocr_enhance_image": settings.vision_ocr_enhance_image,
            "vision_ocr_cleanup_pass": settings.vision_ocr_cleanup_pass,
            "vision_ocr_cleanup_model": settings.vision_ocr_cleanup_model,
            "vision_ocr_ollama_url": settings.vision_ocr_ollama_url,
            "vision_ocr_form_mode": settings.vision_ocr_form_mode,
            # MCP settings
            "enable_mcp": settings.enable_mcp,
            "mcp_server_id": settings.mcp_server_id,
            "mcp_default_collection": settings.mcp_default_collection,
            "mcp_top_k": settings.mcp_top_k,
            "mcp_mode": settings.mcp_mode,
            "mcp_semantic_weight": settings.mcp_semantic_weight,
            "mcp_include_sources": settings.mcp_include_sources,
            "mcp_max_source_length": settings.mcp_max_source_length,
            "mcp_ai_provider": settings.mcp_ai_provider,
            "mcp_ollama_model": settings.mcp_ollama_model,
            # Privacy / PII redaction settings
            "enable_pii_redaction": settings.enable_pii_redaction,
            "pii_redaction_style": settings.pii_redaction_style,
            "pii_score_threshold": settings.pii_score_threshold,
            # UI feature flags
            "enable_chat_tab": settings.enable_chat_tab,
        }

        # Override with database values if present
        db_config = app_db.get_all_config()
        for key in config.keys():
            if key in db_config:
                config[key] = db_config[key]

        return config

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update configuration values.

        Args:
            updates: Dictionary of config key-value pairs to update

        Returns:
            Dictionary with:
                - success (bool): Whether update succeeded
                - requires_restart (bool): Whether server restart is needed
                - requires_reindex (bool): Whether re-indexing is needed
                - updated_fields (list): List of fields that were updated
                - errors (list): List of error messages if any
        """
        result = {
            "success": True,
            "requires_restart": False,
            "requires_reindex": False,
            "updated_fields": [],
            "errors": [],
        }

        # Fields that require restart
        restart_fields = {"embedding_model", "host", "port"}
        # Fields that require re-indexing
        reindex_fields = {"embedding_model", "chunk_size", "chunk_overlap"}
        # OCR fields that can be applied at runtime (no restart needed)
        ocr_fields = {
            "enable_ocr", "ocr_max_pages", "ocr_max_file_mb",
            "vision_ocr_provider", "vision_ocr_model", "vision_ocr_api_key",
            "vision_ocr_dpi", "vision_ocr_enhance_image",
            "vision_ocr_cleanup_pass", "vision_ocr_cleanup_model",
            "vision_ocr_ollama_url", "vision_ocr_form_mode",
        }
        mcp_fields = {"enable_mcp", "mcp_server_id", "mcp_default_collection", "mcp_top_k",
                      "mcp_mode", "mcp_semantic_weight", "mcp_include_sources",
                      "mcp_max_source_length", "mcp_ai_provider", "mcp_ollama_model"}
        privacy_fields = {"enable_pii_redaction", "pii_redaction_style", "pii_score_threshold"}
        ui_fields = {"enable_chat_tab"}

        # Validate updates
        valid_fields = {
            "embedding_model", "chunk_size", "chunk_overlap",
            "default_top_k", "max_top_k",
            "enable_ocr", "ocr_max_pages", "ocr_max_file_mb",
            "vision_ocr_provider", "vision_ocr_model", "vision_ocr_api_key",
            "vision_ocr_dpi", "vision_ocr_enhance_image",
            "vision_ocr_cleanup_pass", "vision_ocr_cleanup_model",
            "vision_ocr_ollama_url", "vision_ocr_form_mode",
            "enable_mcp", "mcp_server_id", "mcp_default_collection",
            "mcp_top_k", "mcp_mode", "mcp_semantic_weight",
            "mcp_include_sources", "mcp_max_source_length",
            "mcp_ai_provider", "mcp_ollama_model",
            "enable_pii_redaction", "pii_redaction_style", "pii_score_threshold",
            "enable_chat_tab",
        }

        for key in updates.keys():
            if key not in valid_fields:
                result["errors"].append(f"Invalid config field: {key}")
                result["success"] = False

        if not result["success"]:
            return result

        # Store in database (primary persistence)
        try:
            for key, value in updates.items():
                app_db.set_config(key, value)
        except Exception as e:
            result["errors"].append(f"Failed to update database: {e}")
            result["success"] = False
            return result

        # Also update .env file for restart compatibility
        try:
            self._update_env_file(updates)
        except Exception as e:
            # Non-fatal - database is source of truth
            logger.warning(f"Failed to update .env file: {e}")

        # Update runtime settings (for non-restart fields)
        ocr_settings_changed = False
        for key, value in updates.items():
            if key in restart_fields:
                result["requires_restart"] = True
            if key in reindex_fields:
                result["requires_reindex"] = True

            result["updated_fields"].append(key)

            # Update runtime settings if no restart required
            if key not in restart_fields:
                try:
                    setattr(settings, key, value)
                    if key in ocr_fields:
                        ocr_settings_changed = True
                except Exception as e:
                    result["errors"].append(f"Failed to update {key}: {e}")
                    result["success"] = False

        # Reload document extractor if any OCR settings changed
        if ocr_settings_changed:
            try:
                from services.indexer_manager import indexer_manager
                indexer_manager.reload_document_extractor()
            except Exception as e:
                logger.warning(f"Failed to reload document extractor after OCR settings change: {e}")

        return result

    def _update_env_file(self, updates: Dict[str, Any]):
        """Update .env file with new values."""
        env_lines = []

        # Read existing .env file if it exists
        if self.env_file.exists():
            with open(self.env_file, "r") as f:
                env_lines = f.readlines()

        # Convert updates to uppercase env var names
        env_updates = {k.upper(): str(v) for k, v in updates.items()}

        # Update or append values
        updated_keys = set()
        for i, line in enumerate(env_lines):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if "=" in line:
                key = line.split("=", 1)[0].strip()
                if key in env_updates:
                    env_lines[i] = f"{key}={env_updates[key]}\n"
                    updated_keys.add(key)

        # Append new values that weren't found
        for key, value in env_updates.items():
            if key not in updated_keys:
                env_lines.append(f"{key}={value}\n")

        # Write back to file
        with open(self.env_file, "w") as f:
            f.writelines(env_lines)


    def get_embedding_models(self) -> list:
        """Get list of recommended embedding models.

        Returns list of dicts with model info:
            - name: Model name
            - description: Brief description
            - dimensions: Embedding dimensions
            - size_mb: Approximate model size in MB
            - speed: Relative speed (fast, medium, slow)
            - quality: Relative quality (good, better, best)
        """
        return [
            {
                "name": "BAAI/bge-base-en-v1.5",
                "description": "Current default - strong English retrieval quality",
                "dimensions": 768,
                "size_mb": 420,
                "speed": "medium",
                "quality": "best",
                "language": "English",
            },
            {
                "name": "Qwen/Qwen3-Embedding-0.6B",
                "description": "Instruction-aware retrieval model - stronger but heavier than MiniLM",
                "dimensions": 1024,
                "size_mb": 1300,
                "speed": "slow",
                "quality": "best",
                "language": "Multilingual",
            },
            {
                "name": "all-MiniLM-L6-v2",
                "description": "Fast and efficient baseline",
                "dimensions": 384,
                "size_mb": 90,
                "speed": "fast",
                "quality": "good",
                "language": "English",
            },
            {
                "name": "all-mpnet-base-v2",
                "description": "High quality - slower but more accurate",
                "dimensions": 768,
                "size_mb": 420,
                "speed": "slow",
                "quality": "best",
                "language": "English",
            },
            {
                "name": "paraphrase-MiniLM-L3-v2",
                "description": "Fastest - lower quality but very fast",
                "dimensions": 384,
                "size_mb": 60,
                "speed": "very fast",
                "quality": "fair",
                "language": "English",
            },
            {
                "name": "paraphrase-multilingual-MiniLM-L12-v2",
                "description": "Multilingual support - 50+ languages",
                "dimensions": 384,
                "size_mb": 470,
                "speed": "medium",
                "quality": "better",
                "language": "Multilingual",
            },
            {
                "name": "all-MiniLM-L12-v2",
                "description": "Balanced - good quality and speed",
                "dimensions": 384,
                "size_mb": 120,
                "speed": "medium",
                "quality": "better",
                "language": "English",
            },
        ]


# Global instance
config_manager = ConfigManager()


