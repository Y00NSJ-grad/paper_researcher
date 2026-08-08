from __future__ import annotations

from typing import Any

from radar.models import PaperCandidate, ScoredPaper
from radar.text import contains_term


def score_paper(paper: PaperCandidate, config: dict[str, Any]) -> ScoredPaper:
    title = paper.title or ""
    body = f"{paper.title}\n{paper.abstract or ''}"
    title_multiplier = float(config.get("scoring", {}).get("title_multiplier", 1.35))
    tags: dict[str, list[str]] = {"methods": [], "domains": [], "tasks": []}
    reasons: list[str] = []
    score = 0.0

    for axis in ("methods", "domains", "tasks"):
        for tag, definition in config.get(axis, {}).items():
            matched = [term for term in definition.get("terms", []) if contains_term(body, term)]
            if not matched:
                continue
            tags[axis].append(tag)
            weight = float(definition.get("weight", 0))
            title_hit = any(contains_term(title, term) for term in matched)
            contribution = weight * (title_multiplier if title_hit else 1.0)
            score += contribution
            reasons.append(f"{axis}:{tag} (+{contribution:.1f})")

    populated_axes = sum(bool(tags[axis]) for axis in tags)
    if populated_axes >= 2:
        bonus = float(config.get("scoring", {}).get("cross_axis_bonus", 12))
        score += bonus
        reasons.append(f"cross-axis match (+{bonus:.1f})")

    if paper.code_url:
        bonus = float(config.get("scoring", {}).get("code_bonus", 8))
        score += bonus
        reasons.append(f"code available (+{bonus:.1f})")

    if contains_term(title, "survey") or contains_term(title, "review"):
        bonus = float(config.get("scoring", {}).get("survey_bonus", 6))
        score += bonus
        reasons.append(f"survey/review (+{bonus:.1f})")

    max_score = float(config.get("scoring", {}).get("max_score", 100))
    return ScoredPaper(paper, round(min(score, max_score), 2), tags, reasons)

