"""Classify a launch log: which milestones were reached, which known failure signatures appear, what to try."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from ..core.paths import catalog_dir

ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class Finding:
    id: str
    severity: str
    diagnosis: str
    suggest: list[str]
    evidence: str
    use_alt: bool = False


@dataclass
class TriageResult:
    state: str  # RUNNING | EXITED | NEVER_STARTED | UNKNOWN
    milestone: str | None
    milestones: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    fps: float | None = None

    @property
    def verdict(self) -> str:
        fatal = [f for f in self.findings if f.severity == "fatal"]
        if self.state == "RUNNING" and not fatal:
            return "pass"
        return "fail" if fatal or self.state in ("EXITED", "NEVER_STARTED") else "unknown"

    def suggestions(self) -> list[str]:
        out = []
        for f in self.findings:
            for s in f.suggest:
                if s not in out:
                    out.append(s)
        return out


_db = None


def database() -> dict:
    global _db
    if _db is None:
        _db = yaml.safe_load((catalog_dir() / "triage.yaml").read_text(encoding="utf-8"))
    return _db


def game_lines(log: str, package: str | None = None) -> list[str]:
    """Strip colour codes; when possible keep only lines of the game's process (plus Lepton's own lines)."""
    lines = [ANSI.sub("", l) for l in log.splitlines()]
    if not package:
        return lines
    pids = set()
    for l in lines:
        m = re.search(r"Start proc (\d+):" + re.escape(package), l)
        if m:
            pids.add(m[1])
    if not pids:
        return lines
    out = []
    for l in lines:
        f = l.split()
        # logcat threadtime: date time pid tid level tag: msg
        if len(f) > 3 and (f[2] in pids or not f[2].isdigit()):
            out.append(l)
        elif "lepton" in l.lower() or "APP_ACTIVITY" in l:
            out.append(l)
    return out


def triage(log: str, state: str = "UNKNOWN", package: str | None = None) -> TriageResult:
    db = database()
    kind = "pcvr" if package and package.startswith("rift.") else "quest"  # Proton/Revive logs vs Lepton logcat
    lines = game_lines(log, package) if kind == "quest" else [ANSI.sub("", l) for l in log.splitlines()]
    text = "\n".join(lines)
    res = TriageResult(state, None)
    for m in db["milestones"]:
        if m.get("kind", "quest") != kind:
            continue
        if re.search(m["pattern"], text):
            res.milestones.append(m["label"])
            res.milestone = m["label"]
    for sig in db["signatures"]:
        if sig.get("kind", "quest") != kind:
            continue
        hit = re.search(sig["pattern"], text)
        if hit:
            line = next((l for l in lines if re.search(sig["pattern"], l)), hit.group(0))
            res.findings.append(Finding(sig["id"], sig["severity"], sig["diagnosis"], list(sig.get("suggest") or []),
                                        line.strip()[:300], bool(sig.get("use_alt"))))
    fps = re.findall(r"pacing: ([0-9.]+) fps", text)
    if fps:
        res.fps = float(fps[-1])
    return res
