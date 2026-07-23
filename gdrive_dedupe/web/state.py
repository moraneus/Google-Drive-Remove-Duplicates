from __future__ import annotations

import threading
from dataclasses import dataclass, field

from gdrive_dedupe.core.models import ScanResult

BUSY_STATUSES = {"connecting", "scanning"}


@dataclass
class ScanState:
    status: str = "idle"
    progress: dict = field(default_factory=dict)
    result: ScanResult | None = None
    error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def begin(self, status: str) -> bool:
        with self._lock:
            if self.status in BUSY_STATUSES:
                return False
            self.status = status
            self.progress = {}
            self.error = ""
            return True

    def set_progress(self, **kwargs) -> None:
        with self._lock:
            self.progress.update(kwargs)

    def finish(self, result: ScanResult | None) -> None:
        with self._lock:
            self.status = "done"
            self.result = result

    def fail(self, message: str) -> None:
        with self._lock:
            self.status = "error"
            self.error = message

    def snapshot(self) -> dict:
        with self._lock:
            return {"status": self.status, "progress": dict(self.progress), "error": self.error}
