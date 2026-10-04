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
from ..errors import explain, is_connection_error
from ..i18n import tr

MAX_FRAME_RETRIES = 5  # a job that keeps losing the Frame fails after this many tries

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
    needs_frame: bool = False  # uses the Frame: a lost connection pauses the queue instead of failing the job
    retries: int = 0  # times this job went back to the queue after losing the Frame

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
        self.paused: str | None = None  # why the queue waits (e.g. "frame": the Frame dropped off the network)
        self._worker = threading.Thread(target=self._loop, daemon=True, name="frameport-jobs")
        self._worker.start()

    # ------------------------------------------------------------------ public
    def subscribe(self, fn: Callable[[Job | None], None]) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Callable[[Job | None], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def submit(self, job: Job) -> Job:
        job.reporter.subscribe(lambda ev, j=job: self._event(j, ev))
        with self._cv:
            self.jobs.append(job)
            self._queue.append(job)
            self._cv.notify()
        self._notify(job, force=True)
        return job

    def pause(self, reason: str) -> None:
        with self._cv:
            self.paused = reason
        self._notify(None, force=True)

    def resume(self) -> None:
        with self._cv:
            self.paused = None
            self._cv.notify()
        self._notify(None, force=True)

    def has_frame_work(self) -> bool:
        """A job that uses the Frame is running or waiting (keep the Frame awake meanwhile)."""
        return any(j.active and j.needs_frame for j in self.jobs)

    def current(self) -> Job | None:
        return next((j for j in self.jobs if j.state == "running"), None)

    def pending(self) -> list[Job]:
        return [j for j in self.jobs if j.state == "queued"]

    def busy_with(self, package: str) -> Job | None:
        return next((j for j in self.jobs if j.active and j.package == package), None)

    def recent(self, limit: int = 20) -> list[Job]:
        """The running job, then every waiting job in queue order, then up to `limit` finished jobs (newest first).
        Newest-first for all of them put the running job below the whole queue (or past the limit)."""
        jobs = list(self.jobs)
        running = [j for j in jobs if j.state == "running"]
        queued = sorted((j for j in jobs if j.state == "queued"), key=lambda j: j.created)
        done = sorted((j for j in jobs if not j.active), key=lambda j: -(j.finished or j.created))
        return running + queued + done[:limit]

    def clear_finished(self) -> None:
        with self._cv:
            self.jobs = [j for j in self.jobs if j.active]
        self._notify(None, force=True)

    def cancel(self, job: Job) -> None:
        with self._cv:
            if job.state == "queued":
                self._queue.remove(job)
                if not self._queue:
                    self.paused = None  # nothing left to wait for
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
    def _wait_for_frame(self, job: Job, exc: BaseException) -> None:
        """The Frame went away mid-job: put the job back at the front and pause until it's reachable again (the
        app resumes the queue after reconnecting). Uploads continue where they stopped."""
        applog.log.info("job %r: lost the Frame (%s: %s); queue paused", job.title, type(exc).__name__, exc)
        with self._cv:
            job.retries += 1
            job.state, job.started, job.fraction, job.speed = "queued", None, None, ""
            job.stage = tr("Waiting for the Frame")
            job.log.append(f"lost the Frame ({type(exc).__name__}: {exc}); waiting to continue")
            job.version += 1
            self._queue.insert(0, job)
            self.paused = "frame"
        self._notify(job, force=True)

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
                while not self._queue or self.paused:
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
                if job.needs_frame and is_connection_error(exc) and job.retries < MAX_FRAME_RETRIES \
                        and not job.reporter.cancelled.is_set():
                    self._wait_for_frame(job, exc)
                    continue
                traceback.print_exc()
                applog.log.exception("job %r failed", job.title)
                job.state, job.error = "failed", explain(exc)
                job.log.append(f"error: {type(exc).__name__}: {exc}")  # the exact error stays in the log
            job.finished = time.time()
            job.version += 1
            applog.log.info("job %r (%s, %s): %s", job.title, job.kind, job.package or "-", job.state)
            if self.save_logs:
                applog.save_job_log(job.kind, job.package, job.state, job.text())
            self._notify(job, force=True)


_shared: JobManager | None = None
_shared_lock = threading.Lock()


def shared() -> JobManager:
    """The process's one job queue. Flet builds a new app object for every window session (a reconnect after sleep
    or a reload): with a queue per session, the old session's job kept running unseen and the same game could be
    built twice at once in the same work folder (both builds then failed)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = JobManager()
        return _shared
