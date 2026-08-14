"""What each source will actually be asked, derived without making a request.

Every entry here is built from the same functions the collectors call at
collection time, so the preview cannot drift from the real request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from radar.collectors import arxiv as arxiv_module
from radar.collectors.arxiv import MAX_FEED_RESULTS, ArxivCollector
from radar.collectors.common import anchors_from_config, net_terms
from radar.collectors.huggingface import HuggingFaceCollector
from radar.collectors.ieee_xplore import IEEE_JOURNALS, IeeeXploreCollector
from radar.collectors.openalex import OpenAlexCollector
from radar.collectors.openreview import OpenReviewCollector
from radar.collectors.semantic_scholar import SemanticScholarCollector

# Sources that send each query verbatim, one request per query.
PER_QUERY = "per_query"
# Sources that ignore the individual queries and cast one wide net, then route
# the results back to a query for provenance.
NET = "net"


def query_plan(
    config: dict[str, Any],
    queries: list[str],
    since: datetime,
    limit_per_query: int = 25,
    ieee_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Describe the request every source would make for `queries`."""
    anchors = anchors_from_config(config)
    net = net_terms(anchors, queries)
    plan: list[dict[str, Any]] = []

    for name, endpoint, note, builder in (
        (
            OpenAlexCollector.name,
            OpenAlexCollector.endpoint,
            "쿼리 문자열이 `search`로 그대로 전달됩니다.",
            lambda query: OpenAlexCollector.search_params(query, since, limit_per_query),
        ),
        (
            SemanticScholarCollector.name,
            SemanticScholarCollector.endpoint,
            "bulk 엔드포인트가 `-`를 NOT으로 읽기 때문에 하이픈을 공백으로 바꿔 보냅니다.",
            lambda query: SemanticScholarCollector.search_params(query, since, limit_per_query),
        ),
        (
            OpenReviewCollector.name,
            OpenReviewCollector.endpoint,
            "쿼리 문자열이 `query`로 그대로 전달됩니다.",
            lambda query: OpenReviewCollector.search_params(query, since, limit_per_query),
        ),
    ):
        requests = []
        for query in queries:
            params = builder(query)
            requests.append(
                {
                    "query": query,
                    "sent": str(params.get("search") or params.get("query") or ""),
                    "params": {key: str(value) for key, value in params.items()},
                }
            )
        plan.append(
            {
                "source": name,
                "mode": PER_QUERY,
                "endpoint": endpoint,
                "note": note,
                "requests": requests,
                "request_count": len(queries),
                "enabled": True,
            }
        )

    plan.append(
        {
            "source": ArxivCollector.name,
            "mode": NET,
            "endpoint": ArxivCollector.endpoint,
            "note": (
                "쿼리를 그대로 보내지 않습니다. arXiv는 절들을 AND로 묶어서 "
                "단어 단위 쿼리는 48시간 창에서 0건이 됩니다. 대신 아래 하나의 "
                "OR 그물을 날짜 범위와 함께 보내고, 결과를 쿼리에 되배정합니다."
            ),
            "expression": arxiv_module.search_expression(anchors, queries, since),
            "net_terms": net,
            "request_count": 1,
            "max_results": min(limit_per_query * max(len(queries), 1), MAX_FEED_RESULTS),
            "enabled": True,
        }
    )

    plan.append(
        {
            "source": HuggingFaceCollector.name,
            "mode": NET,
            "endpoint": HuggingFaceCollector.endpoint,
            "note": (
                "Daily Papers 피드에는 검색 파라미터가 없습니다. 날짜별 피드를 "
                "받아 아래 용어 중 하나라도 포함하는 논문만 남기고, 결과를 "
                "쿼리에 되배정합니다."
            ),
            "net_terms": net,
            "request_count": None,
            "enabled": True,
        }
    )

    plan.append(
        {
            "source": IeeeXploreCollector.name,
            "mode": PER_QUERY,
            "endpoint": IeeeXploreCollector.endpoint,
            "note": (
                "저널마다 한 번씩, 쿼리를 `querytext`로 보냅니다. "
                "IEEE_XPLORE_ENABLED와 API key가 모두 설정된 경우에만 실행됩니다."
            ),
            "journals": sorted(IEEE_JOURNALS),
            "requests": [
                {
                    "query": query,
                    "sent": query,
                    "params": {
                        key: str(value)
                        for key, value in IeeeXploreCollector.search_params(
                            query, next(iter(IEEE_JOURNALS.values())), limit_per_query
                        ).items()
                    },
                }
                for query in queries
            ],
            "request_count": len(queries) * len(IEEE_JOURNALS),
            "enabled": ieee_enabled,
        }
    )
    return plan
