# Architecture

```
            ┌────────────── UI (Flet, ui/app.py) ─────────────┐   ┌── CLI (cli.py) ──┐
            └───────────────────────┬─────────────────────────┘   └────────┬─────────┘
                                    ▼                                      ▼
                          pipeline.py  (add → suggest → build → install → test)
      ┌──────────────┬──────────────┼───────────────┬───────────────┬──────────────┐
  sources/      analysis/      recommend/        build.py        targets/       validate/
  quest_dump    detect, elf    catalog, engine   overport →      base.Target    static, device,
                stubgen        (+ catalog/*.yaml) patches(apk) →  frame_lepton   triage (+triage.yaml)
                                                  apk/sign        revive (todo)
                                    │                │                 │
                              patches/ registry   tools/ toolchain   frame/ ssh, discovery, pairing
                              overport, frame/*,  (JRE, overport,    install/installer ──► agent (on Frame)
                              settings            apksigner)
```

## Data flow for one game
1. **Source** (`sources/quest_dump.py`): folder with an APK and optional `<package>/` or `obb/` data.
2. **Analysis** (`analysis/detect.py`): package/label/version (pyaxmlparser), ABIs, engine, XR API, graphics API,
   direct-VrApi, GLAD/eglGetProcAddress, Unity MSAA levels, Meta permissions, telemetry references.
3. **Recipe** (`recommend/engine.py`): every patch's `detect()` suggests itself with a reason; a catalog entry (exact
   known-good recipe) overrides heuristics. The UI shows toggles; the user confirms.
4. **Build** (`build.py`): overport (defaults + extras) → apk-stage patches in `order` on an `ApkWorkspace` → apksigner
   (with the package's own keystore) → static validation. Optional alternate build (e.g. without Unreal ForceQuit).
5. **Install** (`install/installer.py` + `agent/frameport_agent.py`): `prepare` (paths, what's already there) → SFTP
   uploads with resume → `finalize` (move into place, settings.conf/framebridge.conf, device files, launch.sh,
   deployment.json, artwork) → `shortcuts` (detached systemd unit stops Steam, writes shortcuts.vdf + grid art,
   restarts Steam).
6. **Test** (`validate/device.py`): agent `launch_test` (systemd-run launch.sh, wait, stop) → fetch launch.log →
   `triage.py` (milestones + signatures → suggested patches) → UI offers "apply suggestions and rebuild".

## Extending
- **New fix**: `patches/frame/<name>.py` with a `Patch` subclass (`detect`, `apply`, optional `validate`), plus a
  signature in `catalog/triage.yaml` and a PLAYBOOK row. Stages: `overport` | `apk` | `install`.
- **New heuristic**: put it in the patch's `detect()` (and `applies()` for visibility), with the evidence in the
  reason text; check `scripts/eval_heuristics.py` still reproduces the catalog and add a test in
  `tests/test_heuristics.py`.
- **New game recipe**: `catalog/games/<package>.yaml` (or "Save as known-good" in the GUI, which writes to the user
  catalog). Publish recipes by serving a folder with `index.json` and pointing `FRAMEPORT_CATALOG_URL` at it.
- **New target** (Revive/PC VR): implement `targets/base.Target`; the pipeline and UI only use that interface.
- **Native binaries** (`artifacts/`): edit `native/…`, run `native/build.sh`, commit the new artifacts + SHA256SUMS,
  run `frameport parity` to see which games change.

## Dynamic data (fetched live, cached, bundled fallback)
overport release + patch list + patch titles (GitHub), Temurin JRE (Adoptium API), apksigner (Google repository index),
store artwork/titles (overport image API), catalog (optional remote), Lepton location/appid (Frame appmanifests),
Steam user (Frame userdata).
