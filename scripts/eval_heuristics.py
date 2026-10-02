#!/usr/bin/env python3
"""Score the patch heuristics against the verified catalog: for every game dump that has a catalog recipe, suggest a
recipe with the catalog switched off and compare the recipe-relevant choices.

    python scripts/eval_heuristics.py "<folder with game dumps>" [--verbose]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from frameport.analysis.detect import analyze  # noqa: E402
from frameport.patches import base  # noqa: E402
from frameport.recommend import catalog, engine  # noqa: E402
from frameport.sources import quest_dump  # noqa: E402


def signature(recipe) -> dict:
    """The parts of a recipe that differ between games (defaults/automatic fixes are excluded)."""
    pats = recipe.patches
    return {
        "overport": sorted(p for p in pats if base.get(p).category == "overport" and not base.get(p).default_on),
        "overport_removed": sorted(p for p in (x.id for x in base.all_patches()
                                               if x.category == "overport" and x.default_on)
                                   if p not in pats),
        "frame": sorted(p for p in pats if base.get(p).category == "frame" and not base.get(p).default_on),
        "adapter": {p.split(".", 1)[1]: v.get("value") for p, v in sorted(pats.items()) if p.startswith("adapter.")},
        "device_files": sorted((pats.get("device.files") or {}).get("files", {})),
        "alt": sorted(recipe.alt_patches),
        "use_alt": recipe.use_alt,
        "status_unsupported": recipe.status == "unsupported",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dumps", type=Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    games = quest_dump.scan(args.dumps)
    total = matched_fields = games_ok = 0
    misses = []
    for g in games:
        a = analyze(g.apk, data_bytes=g.data_bytes())
        if not catalog.lookup(a.package):
            continue
        want = signature(engine.suggest(a, use_catalog=True))
        want["status_unsupported"] = (catalog.lookup(a.package).status == "unsupported"
                                      and "32-bit" in catalog.lookup(a.package).notes)
        got = signature(engine.suggest(a, use_catalog=False))
        diffs = {k: (want[k], got[k]) for k in want if want[k] != got[k]}
        total += len(want)
        matched_fields += len(want) - len(diffs)
        games_ok += not diffs
        if diffs:
            misses.append((a.package, diffs))
        if args.verbose or diffs:
            print(f"{'OK  ' if not diffs else 'DIFF'} {a.package}")
            for k, (w, h) in diffs.items():
                print(f"       {k}: catalog={w}  heuristics={h}")
    n = games_ok + len(misses)
    print(f"\n{games_ok}/{n} games reproduced exactly; {matched_fields}/{total} recipe fields match")
    return 0 if not misses else 1


if __name__ == "__main__":
    sys.exit(main())
