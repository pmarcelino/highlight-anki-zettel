"""Background jobs so the extension never awaits a long HTTP response.

Chrome terminates an MV3 extension service worker when a fetch() response
takes more than 30 s to arrive — a Claude run routinely does — and with it
the outcome toast is lost while the notes/cards are still written. So the
POST endpoints return a job id at once, generation runs on a daemon thread
here, and the extension polls GET /jobs/{id} with short requests.

Process-local by design: a server restart forgets in-flight jobs, which the
poller then sees as 404 and reports.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

log = logging.getLogger(__name__)


class JobError(Exception):
    """Expected failure; its message is shown to the user verbatim."""


class Jobs:
    def __init__(self, keep: int = 100):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._keep = keep

    def submit(self, fn: Callable[[], dict]) -> str:
        job_id = uuid.uuid4().hex
        thread = threading.Thread(target=self._run, args=(job_id, fn), daemon=True)
        with self._lock:
            self._prune()
            self._jobs[job_id] = {"state": "running"}
            self._threads[job_id] = thread
        thread.start()
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def wait(self, job_id: str, timeout: float | None = None) -> None:
        """Block until the job settles (tests)."""
        with self._lock:
            thread = self._threads.get(job_id)
        if thread:
            thread.join(timeout)

    def _run(self, job_id: str, fn: Callable[[], dict]) -> None:
        try:
            outcome = {"state": "done", "result": fn()}
        except JobError as exc:
            outcome = {"state": "failed", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 — surface, never hang the poller
            log.exception("job %s crashed", job_id)
            outcome = {"state": "failed", "detail": f"internal error: {exc}"}
        with self._lock:
            self._jobs[job_id] = outcome
            self._threads.pop(job_id, None)

    def _prune(self) -> None:
        """Drop the oldest settled jobs once over `keep` (dict keeps insertion order)."""
        excess = len(self._jobs) - self._keep
        if excess <= 0:
            return
        for job_id in [j for j, s in self._jobs.items() if s["state"] != "running"][:excess]:
            del self._jobs[job_id]
