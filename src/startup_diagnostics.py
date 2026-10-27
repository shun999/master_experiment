"""One-shot Python stack diagnostics, armed only around startup operations."""

from contextlib import contextmanager
from datetime import datetime
import faulthandler
import math
from pathlib import Path


class StartupDiagnostics:
    def __init__(self, progress, traceback_path, timeout_seconds=20.0, enabled=True):
        if type(enabled) is not bool:
            raise ValueError("Startup diagnostics enabled must be a bool")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Startup traceback timeout must be finite and positive")
        self.progress = progress
        self.path = Path(traceback_path)
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self._file = None
        self._armed = False
        self._closed = False

    def _cancel(self):
        if self._armed:
            # Cancel/join the native watchdog before releasing its file handle.
            faulthandler.cancel_dump_traceback_later()
            self._armed = False

    @contextmanager
    def step(self, label):
        if self._closed:
            raise RuntimeError("Startup diagnostics is already closed")
        if self._armed:
            raise RuntimeError("Startup diagnostic steps cannot be nested")
        self.progress("STARTUP {} START".format(label))
        if self.enabled:
            if self._file is None:
                self._file = self.path.open("a", encoding="utf-8")
            self._file.write("\n{} | STARTUP {} START | traceback after {}s; exit=False\n".format(
                datetime.now().isoformat(timespec="seconds"), label, self.timeout_seconds))
            self._file.flush()
            faulthandler.dump_traceback_later(
                self.timeout_seconds, repeat=False, file=self._file, exit=False)
            self._armed = True
        try:
            yield
        except BaseException as exc:
            self._cancel()
            self.progress("STARTUP {} FAILED: {}: {}".format(label, type(exc).__name__, exc))
            raise
        else:
            self._cancel()
            self.progress("STARTUP {} END".format(label))

    def close(self):
        """Idempotently disarm before closing the file; safe in cleanup paths."""
        self._cancel()
        if self._file is not None:
            self._file.close()
            self._file = None
        self._closed = True
