from __future__ import annotations

import hashlib
import re
import unicodedata

_NON_WORD = re.compile(r"[^a-z0-9]+")
_SPACE = re.compile(r"\s+")


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return _SPACE.sub(" ", _NON_WORD.sub(" ", value.lower())).strip()


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        cleaned = cleaned.removeprefix(prefix)
    return cleaned or None


def normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.rsplit("/", 1)[-1].lower().strip()
    return re.sub(r"v\d+$", "", cleaned) or None


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def contains_term(text: str, term: str) -> bool:
    normalized_text = f" {normalize_title(text)} "
    normalized_term = normalize_title(term)
    if not normalized_term:
        return False
    return f" {normalized_term} " in normalized_text

