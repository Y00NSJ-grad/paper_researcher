from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from radar.models import PaperCandidate
from radar.text import normalize_title


def parse_datetime(value: Any) -> datetime | None:
    """Parse the date variants used by scholarly metadata APIs."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%d %B %Y", "%d %b %Y", "%B %Y", "%Y"):
            try:
                parsed = datetime.strptime(text, pattern).replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def content_value(content: dict[str, Any], key: str, default: Any = None) -> Any:
    """Unwrap OpenReview API v2 content fields while accepting legacy values."""
    value = content.get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How long a collector keeps trying an endpoint that is throttling or down.

    Upstream throttles routinely outlast a handful of seconds, and a collector
    that gives up inside one loses a whole day of papers for that source. The
    defaults spend roughly five minutes before conceding.
    """

    max_attempts: int = 5
    backoff_base_seconds: float = 20.0
    max_backoff_seconds: float = 180.0

    def delay_for(self, attempt: int) -> float:
        delay = min(self.backoff_base_seconds * (2**attempt), self.max_backoff_seconds)
        # Jitter keeps a retry off the exact second a throttle window resets, and
        # keeps repeated daily runs from retrying in lockstep with each other.
        return delay * random.uniform(0.8, 1.2)


def is_retryable(status_code: int) -> bool:
    """Throttling and upstream faults are worth waiting out; 4xx mistakes are not."""
    return status_code == 429 or status_code >= 500


def send_with_retry(
    send: Callable[[], httpx.Response],
    policy: RetryPolicy,
    before: Callable[[], None] | None = None,
    after: Callable[[], None] | None = None,
) -> httpx.Response:
    """Send a request, waiting out throttles and upstream faults.

    `before`/`after` carry each collector's own request spacing bookkeeping, which
    has to run around every attempt including the ones that time out.
    """
    for attempt in range(policy.max_attempts):
        if before:
            before()
        try:
            response = send()
        except httpx.TimeoutException:
            if after:
                after()
            if attempt + 1 >= policy.max_attempts:
                raise
            time.sleep(policy.delay_for(attempt))
            continue
        if after:
            after()

        if not is_retryable(response.status_code):
            response.raise_for_status()
            return response
        if attempt + 1 >= policy.max_attempts:
            response.raise_for_status()

        retry_after = response.headers.get("Retry-After")
        try:
            retry_delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            retry_delay = 0.0
        time.sleep(max(retry_delay, policy.delay_for(attempt)))

    raise RuntimeError("retry loop exhausted")


def _query_parts(query: str) -> list[tuple[str, str]]:
    return re.findall(r'"([^"]+)"|(\S+)', query)


def query_terms(query: str) -> list[str]:
    """Quoted spans stay whole; everything else is one term per word."""
    return [phrase or token for phrase, token in _query_parts(query) if phrase or token]


def query_phrases(query: str) -> list[str]:
    return [phrase for phrase, _ in _query_parts(query) if phrase]


def anchors_from_config(config: dict[str, Any]) -> list[str]:
    """The topical net a collector casts.

    `domains` is the axis the scorer effectively treats as mandatory — nothing
    clears `minimum_relevant` on a method or task match alone — so it is the right
    boundary for a wide net. `extra_anchors` covers topics the queries care about
    that have no domain of their own.
    """
    terms = [
        term
        for definition in (config.get("domains") or {}).values()
        for term in definition.get("terms", [])
    ]
    terms.extend(config.get("extra_anchors") or [])
    return terms


def net_terms(anchors: Sequence[str], queries: Sequence[str]) -> list[str]:
    """The vocabulary to search on, as an OR net rather than a per-query AND.

    ANDing a query's words collapses recall to nothing over a daily window, so
    collectors cast one wide net and let `radar.scoring` judge relevance. Without a
    configured vocabulary the queries themselves are the net.
    """
    if not anchors:
        return [term for query in queries for term in query_terms(query)]
    # Quoted spans are specific enough to widen the net without flooding it.
    return [*anchors, *(phrase for query in queries for phrase in query_phrases(query))]


def query_overlap(candidate: PaperCandidate, query: str) -> float:
    """Share of a query's terms the candidate contains, used to label provenance."""
    terms = query_terms(query)
    if not terms:
        return 0.0
    haystack = normalize_title(f"{candidate.title} {candidate.abstract or ''}")
    return sum(1 for term in terms if normalize_title(term) in haystack) / len(terms)


def route_to_query(candidate: PaperCandidate, queries: Sequence[str]) -> str:
    """Route a candidate to exactly one query, so a run's counts stay honest.

    The net is deliberately wider than any single query, so a paper can be on topic
    while matching none of them; those land in the first bucket and are judged by
    score like everything else.
    """
    best, best_overlap = queries[0], 0.0
    for query in queries:
        overlap = query_overlap(candidate, query)
        if overlap > best_overlap:
            best, best_overlap = query, overlap
    return best
