from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from radar.models import (
    MonthlyTrendAnalysis,
    PaperSummary,
    TrendSection,
    WeeklyInsight,
    WeeklyTrendAnalysis,
)

KST = ZoneInfo("Asia/Seoul")


def _json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def summary_from_row(row: dict) -> PaperSummary | None:
    payload = _json(row.get("summary_json"), None)
    if not payload:
        return None
    return PaperSummary(**payload)


def render_digest(kind: str, rows: list[dict], stats: dict[str, int]) -> str:
    now = datetime.now(KST)
    heading = f"📝 {kind.title()} Paper Radar — {now:%Y-%m-%d}"
    lines = [
        heading,
        "",
        (
            f"Collected: {stats.get('collected', 0)} · "
            f"Relevant: {stats.get('relevant', 0)} · "
            f"New: {stats.get('new', 0)} · "
            f"Source errors: {stats.get('source_errors', 0)}"
        ),
        "",
    ]
    if not rows:
        if kind == "daily" and stats.get("relevant", 0):
            lines.append("No new relevant papers were found in this run.")
        else:
            lines.append("No relevant papers were found in this run.")
        return "\n".join(lines)

    for index, row in enumerate(rows, 1):
        tags = _json(row.get("tags_json"), {})
        tag_line = " · ".join(
            tag for axis in ("domains", "methods", "tasks") for tag in tags.get(axis, [])
        )
        lines.extend(
            [
                f"> {index}. [{row['title']}]({row['primary_url']})",
                "",
                f"Score: **{row['score']:.1f}**" + (f" · {tag_line}" if tag_line else ""),
            ]
        )
        if row.get("venue"):
            lines.append(f"Venue: {row['venue']}")
        summary = summary_from_row(row)
        if summary:
            lines.extend(["", *summary.as_lines()])
        else:
            abstract = (row.get("abstract") or "").strip()
            if abstract:
                preview = abstract[:500] + ("…" if len(abstract) > 500 else "")
                lines.extend(["", f"Abstract: {preview}"])
        links = [f"[Paper]({row['primary_url']})"]
        if row.get("pdf_url"):
            links.append(f"[PDF]({row['pdf_url']})")
        if row.get("code_url"):
            links.append(f"[Code]({row['code_url']})")
        lines.extend(["", " · ".join(links), ""])
    return "\n".join(lines).strip() + "\n"


def trend_counts(rows: Iterable[dict]) -> tuple[Counter[str], Counter[str]]:
    tag_counts: Counter[str] = Counter()
    pair_counts: Counter[str] = Counter()
    for row in rows:
        tags = _json(row.get("tags_json"), {})
        methods = tags.get("methods", [])
        domains = tags.get("domains", [])
        tasks = tags.get("tasks", [])
        for tag in set(methods + domains + tasks):
            tag_counts[tag] += 1
        for domain in domains:
            for method in methods:
                pair_counts[f"{domain} × {method}"] += 1
            for task in tasks:
                pair_counts[f"{domain} × {task}"] += 1
    return tag_counts, pair_counts


def _render_analysis_section(
    title: str,
    section: TrendSection,
    paper_links: dict[str, tuple[str, str]],
) -> list[str]:
    lines = [f"### {title}", "", section.overview, "", "**핵심 동향**", ""]
    lines.extend(f"- {trend}" for trend in section.key_trends)
    lines.extend(["", "**근거**", ""])
    for evidence in section.evidence:
        citations = [
            f"[{paper_links[paper_id][0]}]({paper_links[paper_id][1]})"
            for paper_id in evidence.paper_ids
            if paper_id in paper_links
        ]
        suffix = f" ({'; '.join(citations)})" if citations else ""
        lines.append(f"- {evidence.claim}{suffix}")
    lines.extend(["", "**연구 기회**", ""])
    lines.extend(f"- {item}" for item in section.research_opportunities)
    lines.extend(["", "**한계 및 주의점**", ""])
    lines.extend(f"- {item}" for item in section.limitations)
    return lines


def _evidence_suffix(paper_ids: list[str], paper_links: dict[str, tuple[str, str]]) -> str:
    citations = [
        f"[{paper_links[paper_id][0]}]({paper_links[paper_id][1]})"
        for paper_id in paper_ids
        if paper_id in paper_links
    ]
    return f" ({'; '.join(citations)})" if citations else ""


def _render_weekly_insights(
    title: str,
    insights: list[WeeklyInsight],
    paper_links: dict[str, tuple[str, str]],
) -> list[str]:
    lines = [f"### {title}", ""]
    if not insights:
        return [*lines, "- 근거가 충분한 신호가 없습니다."]
    for item in insights:
        suffix = _evidence_suffix(item.paper_ids, paper_links)
        lines.append(f"- **{item.title}** · `{item.confidence}` — {item.insight}{suffix}")
    return lines


def _render_weekly_analysis(
    analysis: WeeklyTrendAnalysis,
    paper_links: dict[str, tuple[str, str]],
    coverage: dict,
) -> list[str]:
    lines = ["## GPT Weekly Research Pulse", "", analysis.research_pulse, ""]
    lines.extend(
        _render_weekly_insights("Emerging Signals", analysis.emerging_signals, paper_links)
    )
    lines.extend(
        [
            "",
            *_render_weekly_insights(
                "Cross-domain Convergence",
                analysis.cross_domain_convergence,
                paper_links,
            ),
            "",
            "### Papers Worth Reading",
            "",
        ]
    )
    valid_picks = [
        (item, paper_links[item.paper_id])
        for item in analysis.papers_worth_reading
        if item.paper_id in paper_links
    ]
    if valid_picks:
        for item, paper in valid_picks:
            lines.append(f"- **{item.role}** — [{paper[0]}]({paper[1]}): {item.why}")
    else:
        lines.append("- 이번 주 추천할 근거 충분한 논문이 없습니다.")
    lines.extend(
        [
            "",
            *_render_weekly_insights(
                "Research Opportunities", analysis.research_opportunities, paper_links
            ),
            "",
            *_render_weekly_insights("Watchlist for Next Week", analysis.watchlist, paper_links),
            "",
            "### Data Coverage & Confidence",
            "",
            analysis.data_coverage,
            "",
            f"- Pipeline runs: {coverage.get('runs', 0)}",
            f"- Source errors: {coverage.get('source_errors', 0)}",
        ]
    )
    source_papers = coverage.get("source_papers", {})
    if source_papers:
        lines.append(
            "- Source coverage: "
            + " · ".join(f"{source} {count}" for source, count in sorted(source_papers.items()))
        )
    source_failures = coverage.get("source_failures", {})
    if source_failures:
        lines.append(
            "- Failures by source: "
            + " · ".join(f"{source} {count}" for source, count in sorted(source_failures.items()))
        )
    return lines


def render_trend_report(
    kind: str,
    rows: list[dict],
    days: int,
    analysis: MonthlyTrendAnalysis | WeeklyTrendAnalysis | None = None,
    evidence_rows: list[dict] | None = None,
    collection_coverage: dict | None = None,
) -> str:
    now = datetime.now(KST)
    tag_counts, pair_counts = trend_counts(rows)
    lines = [
        f"🗺️ {kind.title()} Trend Map — {now:%Y-%m-%d}",
        "",
        f"Window: last {days} days · Unique papers: {len(rows)}",
        "",
    ]
    if isinstance(analysis, MonthlyTrendAnalysis):
        paper_links = {f"P{row['id']}": (row["title"], row["primary_url"]) for row in rows}
        lines.extend(
            [
                "## GPT Research Trend Analysis",
                "",
                analysis.executive_summary,
                "",
                *_render_analysis_section("Physical AI 부문", analysis.physical_ai, paper_links),
                "",
                *_render_analysis_section("Quantum AI 부문", analysis.quantum_ai, paper_links),
                "",
                *_render_analysis_section("도메인 부문", analysis.domains, paper_links),
                "",
                "## Quantitative Signals",
                "",
            ]
        )
    elif isinstance(analysis, WeeklyTrendAnalysis):
        linked_rows = evidence_rows if evidence_rows is not None else rows
        paper_links = {f"P{row['id']}": (row["title"], row["primary_url"]) for row in linked_rows}
        lines.extend(
            [
                *_render_weekly_analysis(
                    analysis,
                    paper_links,
                    collection_coverage or {},
                ),
                "",
                "## Quantitative Signals",
                "",
            ]
        )
    lines.extend(["> Repeated combinations", ""])
    repeated = [(name, count) for name, count in pair_counts.most_common(15) if count >= 2]
    if repeated:
        lines.extend(f"- {name}: {count} papers" for name, count in repeated)
    else:
        lines.append("- No combination has repeated at least twice yet.")
    lines.extend(["", "> Frequent tags", ""])
    lines.extend(f"- {name}: {count}" for name, count in tag_counts.most_common(15))
    lines.extend(["", "> Strong papers", ""])
    for row in rows[:10]:
        lines.append(f"- [{row['title']}]({row['primary_url']}) — score {row['score']:.1f}")
    return "\n".join(lines).strip() + "\n"


def write_report(output_dir: Path, kind: str, content: str) -> Path:
    now = datetime.now(KST)
    directory = output_dir / kind
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now:%Y-%m-%d}.md"
    path.write_text(content, encoding="utf-8")
    return path
