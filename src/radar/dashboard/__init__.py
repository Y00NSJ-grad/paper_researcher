"""Read-only local dashboard for inspecting the Paper Radar database."""

from radar.dashboard.data import DashboardData
from radar.dashboard.server import build_server, serve

__all__ = ["DashboardData", "build_server", "serve"]
