"""Every bundled catalog file parses (the loader skips broken files silently, so a typo would hide a game)."""
from pathlib import Path

import yaml

from frameport.recommend.catalog import CatalogEntry, catalog_dir


def test_every_catalog_file_parses():
    files = sorted((catalog_dir() / "games").glob("*.yaml"))
    assert files
    for f in files:
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        e = CatalogEntry.from_dict(d, "bundled")
        assert e.package == Path(f).stem, f
