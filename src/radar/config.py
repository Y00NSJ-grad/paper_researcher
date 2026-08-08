from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path
    output_dir: Path
    config_path: Path
    slack_webhook_url: str | None
    openalex_api_key: str | None
    openai_api_key: str | None
    openai_model: str
    contact_email: str | None
    user_agent: str
    semantic_scholar_api_key: str | None = None
    ieee_xplore_api_key: str | None = None
    openreview_token: str | None = None
    huggingface_token: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_path=Path(os.getenv("RADAR_DB_PATH", "data/papers.db")),
            output_dir=Path(os.getenv("RADAR_OUTPUT_DIR", "outputs")),
            config_path=Path(os.getenv("RADAR_CONFIG_PATH", "config/keywords.yml")),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL") or None,
            openalex_api_key=os.getenv("OPENALEX_API_KEY") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6"),
            contact_email=os.getenv("CONTACT_EMAIL") or None,
            user_agent=os.getenv("RADAR_USER_AGENT", "paper-radar/0.1"),
            semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None,
            ieee_xplore_api_key=os.getenv("IEEE_XPLORE_API_KEY") or None,
            openreview_token=os.getenv("OPENREVIEW_TOKEN") or None,
            huggingface_token=os.getenv("HF_TOKEN") or None,
        )


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise TypeError(f"Configuration must be a mapping: {path}")
    return data
