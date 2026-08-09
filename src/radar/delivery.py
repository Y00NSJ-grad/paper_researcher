from __future__ import annotations

import re

import httpx

_MARKDOWN_LINK = re.compile(r"\[([^]]+)]\((https?://[^)]+)\)")


def markdown_to_slack(value: str) -> str:
    value = _MARKDOWN_LINK.sub(r"<\2|\1>", value)
    value = value.replace("**", "*")
    return value


class SlackWebhookDelivery:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.client = httpx.Client(timeout=20)

    def send(self, content: str) -> None:
        slack_text = markdown_to_slack(content)
        chunks = _chunks(slack_text, 2800)
        for chunk in chunks:
            try:
                response = self.client.post(
                    self.webhook_url,
                    json={
                        "text": chunk[:500],
                        "blocks": [
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": chunk},
                            }
                        ],
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError:
                raise RuntimeError("Slack webhook delivery failed") from None


def _chunks(value: str, size: int) -> list[str]:
    paragraphs = value.split("\n\n")
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip()
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > size:
            chunks.append(paragraph[:size])
            paragraph = paragraph[size:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks or [value[:size]]
