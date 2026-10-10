"""Per-sender conversation state and the opt-in subscriber list (aggregate-free: stores a phone/iMessage id, name, place)."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class Subscribers:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"people": {}, "outbox": []}

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.path)

    def get(self, sender):
        with self.lock:
            return self._load()["people"].get(sender)

    def put(self, sender, profile):
        with self.lock:
            data = self._load()
            profile["last_seen"] = datetime.now(timezone.utc).isoformat()
            data["people"][sender] = profile
            self._save(data)

    def delete(self, sender):
        with self.lock:
            data = self._load()
            data["people"].pop(sender, None)
            self._save(data)

    def all(self):
        with self.lock:
            return dict(self._load()["people"])

    def queue(self, items):
        with self.lock:
            data = self._load()
            data["outbox"].extend(items)
            self._save(data)

    def drain(self):
        with self.lock:
            data = self._load()
            out, data["outbox"] = data["outbox"], []
            self._save(data)
            return out
