from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from radar.models import PaperSummary

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


def render_trend_report(kind: str, rows: list[dict], days: int) -> str:
    now = datetime.now(KST)
    tag_counts, pair_counts = trend_counts(rows)
    lines = [
        f"🗺️ {kind.title()} Trend Map — {now:%Y-%m-%d}",
        "",
        f"Window: last {days} days · Unique papers: {len(rows)}",
        "",
        "> Repeated combinations",
        "",
    ]
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

