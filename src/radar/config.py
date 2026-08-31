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


def _block_span(lines: list[str], key: str) -> tuple[int, int] | None:
    """Line range of one top-level mapping, excluding the blank lines after it."""
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if _TOP_LEVEL_KEY.match(line) and line.startswith(f"{key}:")
        ),
        None,
    )
    if start is None:
        return None
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
    return start, end


def replace_block(text: str, key: str, block: str) -> str:
    """Swap one top-level mapping for `block`, leaving every other byte untouched."""
    lines = text.splitlines(keepends=True)
    span = _block_span(lines, key)
    if span is None:
        separator = "" if not text or text.endswith("\n\n") else "\n"
        return f"{text}{separator}{block}"
    start, end = span
    return "".join(lines[:start]) + block + "".join(lines[end:])


def replace_queries_block(text: str, queries: dict[str, list[str]]) -> str:
    return replace_block(text, "queries", _render_queries_block(queries))


def _write_verified(path: Path, updated_text: str, changed: dict[str, Any]) -> None:
    """Re-parse before replacing the file, so a rendering bug cannot corrupt it."""
    original = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updated = yaml.safe_load(updated_text)
    if not isinstance(updated, dict) or any(
        updated.get(key) != value for key, value in changed.items()
    ):
        raise ValueError("The rewritten config did not round-trip; nothing was written")
    untouched = {key: value for key, value in updated.items() if key not in changed}
    if untouched != {key: value for key, value in original.items() if key not in changed}:
        raise ValueError("Rewriting the config changed unrelated settings; nothing was written")

    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(updated_text, encoding="utf-8")
    temporary.replace(path)


def write_queries(path: Path, queries: dict[str, list[str]]) -> dict[str, list[str]]:
    """Persist query edits, verifying the result before it replaces the file."""
    cleaned = validate_queries(queries)
    text = replace_queries_block(path.read_text(encoding="utf-8"), cleaned)
    _write_verified(path, text, {"queries": cleaned})
    return cleaned


# ------------------------------------------------------------------ tag axes

AXES = ("methods", "domains", "tasks")
MAX_TERM_LENGTH = 120
_TAG_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
_YAML_INDICATORS = set("-?:,[]{}#&*!|>'\"%@`")
_TAG_LINE = re.compile(r"^  ([A-Za-z0-9_][A-Za-z0-9_-]*):\s*$")
_FLOW_TERMS_LINE = re.compile(r"^    terms:\s*\[")


def validate_axes(axes: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Reject anything the scorer could not use, before it reaches the file."""
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for axis, entries in axes.items():
        if axis not in AXES:
            raise ValueError(f"Unknown tag axis: {axis!r}")
        if not isinstance(entries, list):
            raise TypeError(f"{axis} must be a list of tags")
        seen_tags: set[str] = set()
        axis_tags: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise TypeError(f"Each {axis} tag must be an object")
            name = str(entry.get("tag", "")).strip()
            if not _TAG_NAME.match(name):
                raise ValueError(
                    f"Tag names use letters, digits, `_` and `-` only: {name or '(empty)'}"
                )
            if name in seen_tags:
                raise ValueError(f"Duplicate tag in {axis}: {name}")
            seen_tags.add(name)

            try:
                weight = float(entry.get("weight", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name}: weight must be a number") from exc
            if not 0 <= weight <= 100:
                raise ValueError(f"{name}: weight must be between 0 and 100")

            raw_terms = entry.get("terms")
            if not isinstance(raw_terms, list):
                raise TypeError(f"{name}: terms must be a list")
            seen_terms: set[str] = set()
            terms: list[str] = []
            for term in raw_terms:
                if not isinstance(term, str):
                    raise TypeError(f"{name}: terms must be strings")
                text = " ".join(term.split())
                if not text:
                    raise ValueError(f"{name}: a term cannot be empty")
                if len(text) > MAX_TERM_LENGTH:
                    raise ValueError(f"{name}: a term cannot exceed {MAX_TERM_LENGTH} characters")
                if text.lower() in seen_terms:
                    raise ValueError(f"{name}: duplicate term {text}")
                seen_terms.add(text.lower())
                terms.append(text)
            if not terms:
                raise ValueError(f"{name}: a tag needs at least one term or it can never match")

            # Integral weights render as `14` rather than `14.0`, matching the file.
            axis_tags.append(
                {"tag": name, "weight": int(weight) if weight.is_integer() else weight,
                 "terms": terms}
            )
        cleaned[axis] = axis_tags
    return cleaned


def _plain_ok(value: str, flow: bool) -> bool:
    """Whether a YAML plain (unquoted) scalar would read back as this string."""
    if not value or value != value.strip():
        return False
    if value[0] in _YAML_INDICATORS:
        return False
    if ": " in value or " #" in value or value.endswith(":"):
        return False
    if flow and any(character in value for character in ",[]{}"):
        return False
    try:
        return yaml.safe_load(value) == value
    except yaml.YAMLError:
        return False


def _scalar(value: str, flow: bool = False) -> str:
    return value if _plain_ok(value, flow) else _quote(value)


def flow_style_tags(text: str, axis: str) -> set[str]:
    """Tags whose `terms` are written inline, so a rewrite can keep that style."""
    lines = text.splitlines(keepends=True)
    span = _block_span(lines, axis)
    if span is None:
        return set()
    start, end = span
    flow: set[str] = set()
    current: str | None = None
    for line in lines[start + 1 : end]:
        match = _TAG_LINE.match(line.rstrip("\n"))
        if match:
            current = match.group(1)
        elif current and _FLOW_TERMS_LINE.match(line):
            flow.add(current)
    return flow


def render_axis_block(axis: str, tags: list[dict[str, Any]], flow: set[str] | None = None) -> str:
    """Render one axis, keeping inline `terms: [...]` for tags that used it."""
    inline = flow or set()
    if not tags:
        return f"{axis}: {{}}\n"
    lines = [f"{axis}:"]
    for entry in tags:
        terms = entry["terms"]
        lines.append(f"  {entry['tag']}:")
        lines.append(f"    weight: {entry['weight']}")
        if entry["tag"] in inline and all(_plain_ok(term, flow=True) for term in terms):
            lines.append("    terms: [" + ", ".join(terms) + "]")
        else:
            lines.append("    terms:")
            lines.extend(f"      - {_scalar(term)}" for term in terms)
    return "\n".join(lines) + "\n"


def write_axes(path: Path, axes: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Persist tag edits, rewriting only the axes whose contents actually changed."""
    cleaned = validate_axes(axes)
    text = path.read_text(encoding="utf-8")
    current = yaml.safe_load(text) or {}

    changed: dict[str, Any] = {}
    for axis, tags in cleaned.items():
        mapping = {
            entry["tag"]: {"weight": entry["weight"], "terms": entry["terms"]} for entry in tags
        }
        if mapping == (current.get(axis) or {}) and axis in current:
            continue
        text = replace_block(text, axis, render_axis_block(axis, tags, flow_style_tags(text, axis)))
        changed[axis] = mapping
    if not changed:
        return cleaned
    _write_verified(path, text, changed)
    return cleaned
