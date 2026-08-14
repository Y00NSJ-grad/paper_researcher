from __future__ import annotations

import hashlib
import os
import re
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
    ieee_xplore_enabled: bool = False
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
            ieee_xplore_enabled=os.getenv("IEEE_XPLORE_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"},
            openreview_token=os.getenv("OPENREVIEW_TOKEN") or None,
            huggingface_token=os.getenv("HF_TOKEN") or None,
        )


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise TypeError(f"Configuration must be a mapping: {path}")
    return data


QUERY_GROUPS = ("daily", "weekly")
MAX_QUERY_LENGTH = 300
_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][\w-]*\s*:")


def config_token(path: Path) -> str:
    """Fingerprint of the file on disk, so a concurrent hand edit is not clobbered."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def validate_queries(queries: dict[str, list[str]]) -> dict[str, list[str]]:
    """Reject anything the pipeline could not use, before it reaches the file."""
    cleaned: dict[str, list[str]] = {}
    for group, entries in queries.items():
        if group not in QUERY_GROUPS:
            raise ValueError(f"Unknown query group: {group!r}")
        if not isinstance(entries, list):
            raise TypeError(f"{group} queries must be a list")
        seen: set[str] = set()
        group_queries: list[str] = []
        for entry in entries:
            if not isinstance(entry, str):
                raise TypeError(f"{group} queries must be strings")
            text = entry.strip()
            if not text:
                raise ValueError("A query cannot be empty")
            if "\n" in text or "\r" in text:
                raise ValueError("A query cannot span lines")
            if len(text) > MAX_QUERY_LENGTH:
                raise ValueError(f"A query cannot exceed {MAX_QUERY_LENGTH} characters")
            if text.count('"') % 2:
                raise ValueError(f"Unbalanced quote in query: {text}")
            if text in seen:
                raise ValueError(f"Duplicate query in {group}: {text}")
            seen.add(text)
            group_queries.append(text)
        cleaned[group] = group_queries
    for group in QUERY_GROUPS:
        cleaned.setdefault(group, [])
    return cleaned


def _quote(value: str) -> str:
    """A YAML single-quoted scalar, which only needs `'` doubled."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _render_queries_block(queries: dict[str, list[str]]) -> str:
    """Rendered by hand to keep the file's existing quoting and indent style.

    `write_queries` re-parses the result and compares it against the input, so a
    mistake here fails loudly instead of corrupting the config.
    """
    lines = ["queries:"]
    for group in QUERY_GROUPS:
        entries = queries.get(group) or []
        if not entries:
            lines.append(f"  {group}: []")
            continue
        lines.append(f"  {group}:")
        lines.extend(f"    - {_quote(entry)}" for entry in entries)
    return "\n".join(lines) + "\n"


def replace_queries_block(text: str, queries: dict[str, list[str]]) -> str:
    """Rewrite only the `queries:` mapping, leaving every other byte untouched."""
    lines = text.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if _TOP_LEVEL_KEY.match(line)
         and line.startswith("queries:")),
        None,
    )
    block = _render_queries_block(queries)
    if start is None:
        separator = "" if not text or text.endswith("\n\n") else "\n"
        return f"{text}{separator}{block}"

    # The block runs until the next line with content in column 0 — the next
    # top-level key, or a comment introducing it, which must survive intact.
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line[0].isspace():
            end = index
            break
    # Rewind past blank lines so the separation before the next key is preserved
    # by the tail rather than replaced along with the block.
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "".join(lines[:start]) + block + "".join(lines[end:])


def write_queries(path: Path, queries: dict[str, list[str]]) -> dict[str, list[str]]:
    """Persist query edits, verifying the result before it replaces the file."""
    cleaned = validate_queries(queries)
    original_text = path.read_text(encoding="utf-8")
    original = yaml.safe_load(original_text) or {}
    updated_text = replace_queries_block(original_text, cleaned)

    updated = yaml.safe_load(updated_text)
    if not isinstance(updated, dict) or updated.get("queries") != cleaned:
        raise ValueError("Rewriting the queries block did not round-trip; nothing was written")
    untouched = {key: value for key, value in updated.items() if key != "queries"}
    if untouched != {key: value for key, value in original.items() if key != "queries"}:
        raise ValueError("Rewriting the queries block changed unrelated settings")

    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(updated_text, encoding="utf-8")
    temporary.replace(path)
    return cleaned
