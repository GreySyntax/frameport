"""Background jobs for the GUI: one worker runs queued jobs in order (the Frame is one SSH connection and builds share
the toolchain), each with its own Reporter. The UI subscribes to changes and renders the activity panel.
No Flet imports here, so it's unit-testable."""
from __future__ import annotations

import itertools
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from ..core import applog
from ..core.events import Cancelled, Event, Reporter

_ids = itertools.count(1)


@dataclass
class Job:
    title: str
    run: Callable[[Job], object]  # does the work with job.reporter; may return a result
    package: str | None = None
    kind: str = "task"  # install | test | build | scan | tool | task
    id: int = field(default_factory=lambda: next(_ids))
    state: str = "queued"  # queued | running | done | failed | cancelled
    stage: str = ""
    message: str = ""
    fraction: float | None = None
    speed: str = ""  # transfers: "42.1 MB/s · ~3 min left"
    checks: list[dict] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)  # every stage seen, in order
    result: object = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    reporter: Reporter = field(default_factory=Reporter)
    version: int = 0  # bumped on every change (the activity panel only rebuilds tiles that changed)
    log_path: str | None = None  # full launch log saved by a launch test
    summary: dict | None = None  # launch-test summary (install/test jobs)
    to: str = "frame"

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")

    def cancel(self) -> None:
        self.reporter.cancelled.set()

    def text(self) -> str:
        """Checks + log as plain text (Copy log, saved job logs, diagnostics bundles)."""
        checks = [("PASS" if c["ok"] else "FAIL" if c["ok"] is False else "WARN") + f"  {c['name']}"
                  + (f": {c['detail']}" if c.get("detail") else "") for c in self.checks]
        return "\n".join([f"{self.title} — {self.state}" + (f": {self.error}" if self.error else ""), "", "Checks:",
                          *checks, "", "Log:", *self.log]
                         + (["", f"Full launch log: {self.log_path}"] if self.log_path else []))

    def _on_event(self, ev: Event) -> None:
        self.version += 1
        if ev.kind == "stage":
            self.stage, self.message, self.fraction, self.speed = ev.message, "", None, ""
            if not self.stages or self.stages[-1] != ev.message:
                self.stages.append(ev.message)
        elif ev.kind == "progress":
            self.fraction = ev.fraction
            self.speed = ev.data.get("speed") or ""
            if ev.message:
                self.message = ev.message
        elif ev.kind == "check":
            self.checks.append({"name": ev.data.get("name"), "ok": ev.data.get("ok"), "detail": ev.message})
        if ev.message and ev.kind in ("log", "stage"):
            self.log.append(time.strftime("%H:%M:%S ", time.localtime(ev.time)) + ev.message)
            if len(self.log) > 4000:
                del self.log[:1000]


class JobManager:
    def __init__(self, on_change: Callable[[Job | None], None] | None = None, throttle: float = 0.2,
                 save_logs: bool = True):
        self.save_logs = save_logs  # finished jobs' logs go to <data>/logs/jobs (diagnostics bundles)
        self.jobs: list[Job] = []
        self._queue: list[Job] = []
        self._cv = threading.Condition()
        self._listeners: list[Callable[[Job | None], None]] = [on_change] if on_change else []
        self._throttle = throttle
        self._last = 0.0
        self._worker = threading.Thread(target=self._loop, daemon=True, name="frameport-jobs")
        self._worker.start()

    # ------------------------------------------------------------------ public
    def subscribe(self, fn: Callable[[Job | None], None]) -> None:
        self._listeners.append(fn)

    def submit(self, job: Job) -> Job:
        job.reporter.subscribe(lambda ev, j=job: self._event(j, ev))
        with self._cv:
            self.jobs.append(job)
            self._queue.append(job)
            self._cv.notify()
        self._notify(job, force=True)
        return job

    def current(self) -> Job | None:
        return next((j for j in self.jobs if j.state == "running"), None)

    def pending(self) -> list[Job]:
        return [j for j in self.jobs if j.state == "queued"]

    def busy_with(self, package: str) -> Job | None:
        return next((j for j in self.jobs if j.active and j.package == package), None)

    def recent(self, limit: int = 20) -> list[Job]:
        return sorted(self.jobs, key=lambda j: (not j.active, -j.created))[:limit]

    def clear_finished(self) -> None:
        with self._cv:
            self.jobs = [j for j in self.jobs if j.active]
        self._notify(None, force=True)

    def cancel(self, job: Job) -> None:
        with self._cv:
            if job.state == "queued":
                self._queue.remove(job)
                job.state, job.finished = "cancelled", time.time()
                job.version += 1
        job.cancel()
        self._notify(job, force=True)

    def wait_idle(self, timeout: float = 30) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if not any(j.active for j in self.jobs):
                return True
            time.sleep(0.05)
        return False

    # ------------------------------------------------------------------ internals
    def _event(self, job: Job, ev: Event) -> None:
        job._on_event(ev)
        self._notify(job, force=ev.kind in ("stage", "check"))

    def _notify(self, job: Job | None, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last < self._throttle:
            return
        self._last = now
        for fn in list(self._listeners):
            try:
                fn(job)
            except Exception:  # a broken listener must not stop jobs
                traceback.print_exc()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                job = self._queue.pop(0)
                job.state, job.started = "running", time.time()
                job.version += 1
            self._notify(job, force=True)
            try:
                job.result = job.run(job)
                job.state = "cancelled" if job.reporter.cancelled.is_set() else "done"
            except Cancelled:
                job.state = "cancelled"
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                applog.log.exception("job %r failed", job.title)
                job.state, job.error = "failed", f"{exc}" or type(exc).__name__
            job.finished = time.time()
            job.version += 1
            applog.log.info("job %r (%s, %s): %s", job.title, job.kind, job.package or "-", job.state)
            if self.save_logs:
                applog.save_job_log(job.kind, job.package, job.state, job.text())
            self._notify(job, force=True)
