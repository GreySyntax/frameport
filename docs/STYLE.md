# Writing style for FramePort's interface

Short rules so every screen, help text and doc sounds the same. Code style is enforced by `ruff`.

## Words
- **Steam Frame** on first mention in a screen or document, **the Frame** after that. **Headset** only for the
  physical device you wear ("put the headset on").
- **Quest game** for Meta Quest apps, **Android app** for ordinary Android apps, **PC VR game** for Windows VR games.
  Say Oculus or Rift only for games that use Oculus's own SDK.
- **Patch** for anything in a game's patch list (never "fix" for the same thing). **Recipe** for a game's chosen
  patches and settings.
- Tools by their names: OVRPort, Revive, Proton, Lepton, SteamVR.
- **Install** (first time), **Update** (a newer build), **Reinstall** (the same build again); **Play** starts a game.

## Form
- Buttons and headings in sentence case ("Add games", "Report a problem…"); one label per action everywhere.
- "…" at the end of a label when the action asks for more input (a dialog, a file picker).
- Sentences end with a period, including tooltips and help texts; labels don't.
- "and", not "&", in text. An em dash (—) for asides, a middle dot (·) between short facts.
- Sizes in GiB/MiB (`i18n.fmt_size`), dates as YYYY-MM-DD HH:MM (`i18n.fmt_datetime`).
- US spelling (customize, analyze, color).
- Plain, technical wording: say what happens ("Steam restarts once"), no marketing adjectives.

## Translations
- Every text the GUI shows goes through `tr("…")` or `tr_n("…", "…", n)` (see `src/frameport/i18n.py`); use
  templates with `.format()`, never f-strings inside `tr()`, and never decide anything from a displayed text.
- After changing texts run `python scripts/i18n_extract.py` (a test checks the template is current).
- CLI output, logs, diagnostics, catalog notes and launch-test diagnoses stay English.
