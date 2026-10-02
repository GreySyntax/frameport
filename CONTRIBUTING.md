# Contributing

Thanks for helping. The quickest contributions:

- **A game works on the Frame?** In FramePort: the game's menu → **Share working config…** opens a prefilled issue
  with its recipe. Accepted configs become built-in recipes.
- **Something doesn't work?** The game's menu → **Report a problem…** saves a diagnostics zip (personal data
  removed) and opens a prefilled issue to attach it to.

## Development
```
uv sync --extra dev          # Python 3.11+, uv (https://docs.astral.sh/uv/)
uv run frameport-gui         # the app
uv run frameport --help      # the command line
uv run pytest                # tests (no headset or game files needed)
uv run ruff check .          # lint
```
- Code and architecture: `CLAUDE.md`, `docs/ARCHITECTURE.md`; per-game symptoms and fixes: `docs/PLAYBOOK.md`.
- Interface wording: `docs/STYLE.md`. GUI texts go through `tr()`; after changing them run
  `python scripts/i18n_extract.py`.
- Translations: copy `src/frameport/locales/template.json` to `<language>.json`, fill in the values (a list
  `[one, other]` for plural entries) and add `"_language": "<the language's own name>"`.
- New fixes are patch modules (`src/frameport/patches/`), with a triage signature (`catalog/triage.yaml`), a PLAYBOOK
  row and a unit test.
- Native binaries in `artifacts/` are rebuilt with `python native/build.py` (downloads the NDK).

## Ground rules
- Use only games you own. Contributions that help bypass DRM, licence or entitlement checks, or that add game files,
  are not accepted.
- Never commit personal data (IP addresses, user names, home paths, Steam IDs).
- By contributing you agree to license your work under GPL-3.0-only.
