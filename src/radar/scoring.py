from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from radar.models import PaperCandidate, ScoredPaper
from radar.text import contains_term

SURVEY_TERMS = ("survey", "review")


def is_survey(title: str) -> bool:
    """The title test that earns the survey/review bonus."""
    return any(contains_term(title, term) for term in SURVEY_TERMS)


@dataclass(slots=True)
class TermMatch:
    """One tag that fired, with every term that caused it."""

    axis: str
    tag: str
    weight: float
    terms: list[str]
    title_terms: list[str]
    multiplier: float
    contribution: float


@dataclass(slots=True)
class Bonus:
    name: str
    amount: float


@dataclass(slots=True)
class ScoreBreakdown:
    """Every step that produced a score, in the order the scorer applied them."""

    score: float
    raw_score: float
    tags: dict[str, list[str]]
    reasons: list[str]
    matches: list[TermMatch] = field(default_factory=list)
    bonuses: list[Bonus] = field(default_factory=list)
    minimum_relevant: float = 20.0
    max_score: float = 100.0

    @property
    def capped(self) -> bool:
        return self.raw_score > self.max_score

    @property
    def relevant(self) -> bool:
        return self.score >= self.minimum_relevant


def explain_score(
    title: str,
    abstract: str | None,
    config: dict[str, Any],
    has_code: bool = False,
) -> ScoreBreakdown:
    """Score a title/abstract pair and record why every point was awarded.

    This is the scorer itself, not a reimplementation: `score_paper` is a thin
    wrapper over it, so the dashboard explains exactly what the pipeline stored.
    """
    body = f"{title}\n{abstract or ''}"
    scoring = config.get("scoring", {})
    title_multiplier = float(scoring.get("title_multiplier", 1.35))
    max_score = float(scoring.get("max_score", 100))
    tags: dict[str, list[str]] = {"methods": [], "domains": [], "tasks": []}
    matches: list[TermMatch] = []
    bonuses: list[Bonus] = []
    reasons: list[str] = []
    score = 0.0

    for axis in ("methods", "domains", "tasks"):
        for tag, definition in config.get(axis, {}).items():
            matched = [term for term in definition.get("terms", []) if contains_term(body, term)]
            if not matched:
                continue
            tags[axis].append(tag)
            weight = float(definition.get("weight", 0))
            title_terms = [term for term in matched if contains_term(title, term)]
            multiplier = title_multiplier if title_terms else 1.0
            contribution = weight * multiplier
            score += contribution
            reasons.append(f"{axis}:{tag} (+{contribution:.1f})")
            matches.append(
                TermMatch(
                    axis=axis,
                    tag=tag,
                    weight=weight,
                    terms=matched,
                    title_terms=title_terms,
                    multiplier=multiplier,
                    contribution=contribution,
                )
            )

    populated_axes = sum(bool(tags[axis]) for axis in tags)
    if populated_axes >= 2:
        bonus = float(scoring.get("cross_axis_bonus", 12))
        score += bonus
        reasons.append(f"cross-axis match (+{bonus:.1f})")
        bonuses.append(Bonus("cross-axis match", bonus))

    if has_code:
        bonus = float(scoring.get("code_bonus", 8))
        score += bonus
        reasons.append(f"code available (+{bonus:.1f})")
        bonuses.append(Bonus("code available", bonus))

    if is_survey(title):
        bonus = float(scoring.get("survey_bonus", 6))
        score += bonus
        reasons.append(f"survey/review (+{bonus:.1f})")
        bonuses.append(Bonus("survey/review", bonus))

    return ScoreBreakdown(
        score=round(min(score, max_score), 2),
        raw_score=round(score, 2),
        tags=tags,
        reasons=reasons,
        matches=matches,
        bonuses=bonuses,
        minimum_relevant=float(scoring.get("minimum_relevant", 20)),
        max_score=max_score,
    )


def score_paper(paper: PaperCandidate, config: dict[str, Any]) -> ScoredPaper:
    breakdown = explain_score(
        paper.title or "", paper.abstract, config, has_code=bool(paper.code_url)
    )
    return ScoredPaper(paper, breakdown.score, breakdown.tags, breakdown.reasons)
