"""A loopback-only HTTP server for the dashboard.

Stdlib only and bound to 127.0.0.1 by default: the dashboard adds no runtime
dependency to a pipeline that otherwise runs headless under systemd. Reads are
plain GETs; the write routes (feedback verdicts, query and tag edits) are POSTs
guarded by the checks in `_csrf_reason`.
"""

from __future__ import annotations

import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from radar.config import Settings
from radar.dashboard.data import ConfigChanged, DashboardData
from radar.storage import PaperStore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
PAPER_PATH = re.compile(r"^/api/papers/(\d+)$")
FEEDBACK_PATH = re.compile(r"^/api/papers/(\d+)/feedback$")
REPORT_PATH = re.compile(r"^/api/reports/([^/]+)/([^/]+)$")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
# Large enough for a whole keywords.yml worth of tags and terms, small enough
# that a runaway request is still refused.
MAX_BODY = 64 * 1024


def _first(values: dict[str, list[str]], key: str, default: str = "") -> str:
    return values.get(key, [default])[0]


def _int(values: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(_first(values, key, str(default)))
    except ValueError:
        return default


def _float(values: dict[str, list[str]], key: str, default: float) -> float:
    try:
        return float(_first(values, key, str(default)))
    except ValueError:
        return default


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "paper-radar-dashboard"
    protocol_version = "HTTP/1.1"
    data: DashboardData

    def log_message(self, format: str, *args) -> None:  # stdlib signature
        logger.debug("%s - %s", self.address_string(), format % args)

    # ------------------------------------------------------------------ plumbing

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)

    def _is_local(self) -> bool:
        """Block DNS-rebinding: only loopback Host headers may reach the API."""
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return not host or hostname in LOCAL_HOSTS

    def _csrf_reason(self) -> str | None:
        """Why this write must be refused, or None if it may proceed.

        A page on any origin can make the browser POST to this port. Requiring a
        JSON content type forces a CORS preflight, which is never answered (there
        is no do_OPTIONS), and any Origin that does show up must be loopback.
        """
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in LOCAL_HOSTS:
            return "Cross-origin writes are refused."
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != "application/json":
            return "Writes require a application/json body."
        return None

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.close_connection = True
            raise ValueError("Request body is too large.")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise TypeError("Request body must be a JSON object.")
        return payload

    # -------------------------------------------------------------------- routing

    def do_HEAD(self) -> None:  # stdlib signature
        self.do_GET()

    def do_GET(self) -> None:  # stdlib signature
        if not self._is_local():
            self._error(HTTPStatus.FORBIDDEN, "This dashboard only serves loopback requests.")
            return
        parsed = urlparse(self.path)
        route = parsed.path
        params = parse_qs(parsed.query)
        try:
            self._route(route, params)
        except BrokenPipeError:
            pass
        except Exception:
            logger.exception("Dashboard request failed: %s", route)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Request failed; see the server log.")

    def do_POST(self) -> None:  # stdlib signature
        if not self._is_local():
            self.close_connection = True
            self._error(HTTPStatus.FORBIDDEN, "This dashboard only serves loopback requests.")
            return
        reason = self._csrf_reason()
        if reason:
            # The body stays unread, so this connection can no longer be reused.
            self.close_connection = True
            self._error(HTTPStatus.FORBIDDEN, reason)
            return
        route = urlparse(self.path).path
        feedback_match = FEEDBACK_PATH.match(route)
        if not feedback_match and route not in ("/api/queries", "/api/tags"):
            self.close_connection = True
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            payload = self._body()
            if feedback_match:
                paper_id = int(feedback_match.group(1))
                value = payload.get("value")
                if value is None:
                    self._json(self.data.clear_feedback(paper_id))
                else:
                    self._json(self.data.record_feedback(paper_id, value))
            elif route == "/api/queries":
                self._json(
                    self.data.save_queries(
                        payload.get("queries") or {}, str(payload.get("token") or "")
                    )
                )
            else:
                self._json(
                    self.data.save_axes(
                        payload.get("axes") or {}, str(payload.get("token") or "")
                    )
                )
        except ConfigChanged as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (TypeError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except LookupError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
        except BrokenPipeError:
            pass
        except Exception:
            logger.exception("Write failed: %s", route)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Request failed; see the server log.")

    def _route(self, route: str, params: dict[str, list[str]]) -> None:
        data = self.data
        if route in ("/", "/index.html"):
            self._static("index.html")
            return
        if route.startswith("/static/"):
            self._static(route[len("/static/") :])
            return

        if route == "/api/overview":
            self._json(data.overview(days=_int(params, "days", 30)))
            return
        if route == "/api/filters":
            self._json(data.filters())
            return
        if route == "/api/papers":
            self._json(
                data.papers(
                    search=_first(params, "q"),
                    source=_first(params, "source"),
                    tag=_first(params, "tag"),
                    min_score=_float(params, "min_score", 0),
                    days=_int(params, "days", 0),
                    sort=_first(params, "sort", "score"),
                    feedback=_first(params, "feedback"),
                    limit=max(1, min(_int(params, "limit", 50), 500)),
                    offset=max(0, _int(params, "offset", 0)),
                )
            )
            return
        paper_match = PAPER_PATH.match(route)
        if paper_match:
            payload = data.paper(int(paper_match.group(1)))
            if payload is None:
                self._error(HTTPStatus.NOT_FOUND, "No such paper")
            else:
                self._json(payload)
            return
        if route == "/api/queries":
            self._json(data.queries())
            return
        if route == "/api/scoring":
            self._json(data.scoring())
            return
        if route == "/api/trends":
            self._json(data.trends(days=max(1, min(_int(params, "days", 30), 365))))
            return
        if route == "/api/reports":
            self._json(data.reports())
            return
        report_match = REPORT_PATH.match(route)
        if report_match:
            kind = unquote(report_match.group(1))
            name = unquote(report_match.group(2))
            content = data.report(kind, name)
            if content is None:
                self._error(HTTPStatus.NOT_FOUND, "No such report")
            else:
                self._json({"kind": kind, "name": name, "content": content})
            return

        self._error(HTTPStatus.NOT_FOUND, "Not found")


def build_server(settings: Settings, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundDashboardHandler", (DashboardHandler,), {"data": DashboardData(settings)})
    return ThreadingHTTPServer((host, port), handler)


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> None:
    if not settings.db_path.exists():
        # Otherwise every endpoint fails on a missing table before the first run.
        logger.warning("%s does not exist yet; creating an empty database", settings.db_path)
        PaperStore(settings.db_path).initialize()
    httpd = build_server(settings, host, port)
    bound_host, bound_port = httpd.server_address[:2]
    display = f"[{bound_host}]" if ":" in str(bound_host) else bound_host
    print(f"Paper Radar dashboard: http://{display}:{bound_port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
