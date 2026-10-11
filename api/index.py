"""Vercel entry point: runs the ReachCare handler as a serverless function.

vercel.json rewrites every path here as /api/index?__p=<original path>; the original
path is restored before the shared Handler routes the request.
"""
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

server.Handler.app = server.Application(runtime_dir=os.path.join(tempfile.gettempdir(), "reachcare"))


class handler(server.Handler):
    def _restore_path(self):
        parts = urlparse(self.path)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        original = query.pop("__p", None)
        if original is not None:
            rest = urlencode(query)
            self.path = "/" + original.lstrip("/") + (f"?{rest}" if rest else "")

    def do_GET(self):
        self._restore_path()
        super().do_GET()

    def do_POST(self):
        self._restore_path()
        super().do_POST()

    def log_message(self, format, *args):  # Vercel captures stdout; keep logs quiet
        pass
