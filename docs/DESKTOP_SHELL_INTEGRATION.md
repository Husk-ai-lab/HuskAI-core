# Integrating the Husk backend binary in the desktop shell

Oct 12, 2026 · @Marco · covers `husk-backend` 0.1.0 (alpha) and the shell in `huskai-desktop/src-tauri`

This document is the contract between this repository (which **builds** the backend) and the desktop shell (which **downloads, places, starts and stops** it). It exists because the first alpha install failed: the shell ended up with the executable but not the folder next to it (see [What went wrong in the alpha](#what-went-wrong-in-the-alpha)).

Everything under "What the shell does" was read from the shell's source (`config.rs`, `platform.rs`, `release/`, `install/`, `process/supervisor.rs`, `app.rs`). Nothing here was run against a published release: there is no verified end-to-end install from GitHub yet.

## 1. What the artefact is

`uv run python installer.py` produces **one folder**, not one file:

```
dist/husk-backend/
├── husk-backend            # 26 MB: the PyInstaller bootloader + archive (husk-backend.exe on Windows)
└── _internal/              # ~160 MB: libpython3.11.dylib, Python packages, the built UI, data files
```

The executable is useless on its own. At start it loads `_internal/libpython3.11.dylib` (`python3.11.dll` on Windows) from the folder it sits in, and exits with code 255 when it is missing. **The unit of deployment is the whole `husk-backend/` folder.**

One-folder is deliberate (technical plan, antivirus section): no self-extraction to a temp folder, no UPX. A one-file build is possible but is not the default; see [Single-file option](#7-single-file-option).

## 2. Release asset contract

The shell reads the public GitHub releases of `Husk-ai-lab/HuskAI-core` and picks an asset by name. The name is the whole contract.

| Item | Rule |
| --- | --- |
| Asset name | `husk-backend-<version>-<target triple>.tar.gz` or `.zip` |
| Triples | `aarch64-apple-darwin`, `x86_64-apple-darwin`, `x86_64-pc-windows-msvc`, `x86_64-unknown-linux-gnu` |
| Archive content | a single top folder `husk-backend/` holding `husk-backend[.exe]` **and** `_internal/`; or those files directly at the archive root |
| Tag | `v1.4.0` or `1.4.0`; the leading `v` is stripped and the rest is used as a directory name, so it must be a plain name (no `/`, `..`). Non-semver tags such as `alpha-release` work, and are replaced by any real version |
| Draft releases | ignored |
| Pre-releases | offered only on the `beta` channel; `stable` takes the latest non-pre-release |
| Other files | `SHA256SUMS` and similar are ignored: the shell checks the SHA-256 that GitHub itself publishes as the asset's `digest` (§3) |

A release with no asset for the user's triple shows as "not available yet" in the shell; it is not an error.

### Building the archive (per platform, on that platform)

PyInstaller does not cross-compile: build each triple on its own OS and CPU.

```bash
uv run python installer.py                       # builds the UI, then dist/husk-backend/
cd dist
# macOS: COPYFILE_DISABLE stops tar adding ._AppleDouble files that the extractor would unpack
COPYFILE_DISABLE=1 tar -czf husk-backend-0.1.0-aarch64-apple-darwin.tar.gz husk-backend
shasum -a 256 husk-backend-0.1.0-aarch64-apple-darwin.tar.gz >> SHA256SUMS
```

Rules for the archive:
- Include the **folder**, not `husk-backend/husk-backend` alone.
- Keep symlinks as symlinks (default for `tar`). The shell's extractor rejects absolute links and links that point outside the folder.
- No absolute paths, no `..` (the extractor rejects the whole archive).
- Keep the executable bit (the shell also sets it after extracting).
- Sign and notarise **before** archiving (macOS Developer ID + notarisation, Windows Authenticode). `installer.py` never signs; that happens in CI. The alpha build reported `Code signing identity: None`.

### Check the archive before publishing

```bash
mkdir /tmp/check && tar -xzf husk-backend-0.1.0-aarch64-apple-darwin.tar.gz -C /tmp/check
test -f /tmp/check/husk-backend/husk-backend && test -f /tmp/check/husk-backend/_internal/libpython3.11.dylib && echo OK
du -sh /tmp/check/husk-backend                       # ~186 MB expected
```

## 3. What the shell does with it

1. Reads the release list, selects the asset (`select_asset`), downloads to `<app-data>/downloads/` and checks the byte count against the GitHub asset size.
2. Verifies the archive: SHA-256 of the file against the asset's `digest` field (`sha256:<hex>`) that GitHub computes at upload (`Sha256Verifier`). A release asset with no usable digest is **refused**, never installed unchecked. This proves the file is what the release holds, not who published it (a signed manifest is still open).
3. Extracts to `<app-data>/bin/husk/<version>.tmp/`, finds the folder that holds the executable (`locate_root`: the archive root, or its only top folder), makes the executable runnable, then renames that folder to `<app-data>/bin/husk/<version>/`.
4. Records the version in `<app-data>/config.json` (`current` and `previous_version`). After a successful start it deletes every other version folder under `bin/husk/`, so only the current and the previous version are kept.

Resulting layout (macOS `<app-data>` = `~/Library/Application Support/ai.husk.desktop`):

```
bin/husk/<version>/husk-backend
bin/husk/<version>/_internal/...
data/husk/                 # the backend's data folder (husk.db, documents/, …); never inside bin/
logs/husk.log              # the backend's stdout + stderr, appended
logs/shell.log.<date>      # the shell's own log
config.json                # installed component + version
```

"Intact" check (`is_intact`): the executable exists **and** (`_internal/` exists **or** a `libpython*` / `python3*.dll` file sits beside it). The second branch is for older PyInstaller builds that keep libraries beside the executable; a stray extra file no longer makes a broken install look intact.

## 4. How the shell starts and stops the backend

| Aspect | Behaviour |
| --- | --- |
| Command | the executable with `--data-dir <app-data>/data/husk`; working directory = its own folder |
| Port | fixed `8765` (Clerk's redirect URI is exactly `http://127.0.0.1:8765/callback`). If the port is busy the shell refuses to start |
| Readiness | polls `GET http://127.0.0.1:8765/health` for up to 20 s, no proxy; an early exit is reported at once |
| Environment | see below |
| Output | stdout and stderr appended to `logs/husk.log` |
| Stop | SIGTERM, 5 s grace, then kill (Windows: terminate). On Linux the child also gets `PDEATHSIG` so it dies with the shell |
| Failure | a failed start kills the child and reports "backend failed to start"; the shell keeps retrying |

Environment the shell sets for Husk (added to its own environment):

| Variable | Value | Used by the backend (`app/helpers/settings.py`) |
| --- | --- | --- |
| `HUSK_PORT` | `8765` | listen port |
| `HUSK_DATA_DIR` | `<app-data>/data/husk` | data folder (same path the backend would pick by default on this OS) |
| `HUSK_ENV` | `prod` | environment name |
| `HUSK_LAUNCH_SECRET` | random 64 hex chars per launch, memory only | `launch_secret` (shell ↔ backend handshake) |
| `HUSK_STUDIO_UI_URL`, `HUSK_STUDIO_OTLP_URL`, `HUSK_STUDIO_MCP_URL` | only when Studio is up | Studio links, traces, MCP self-heal |

## 5. What the backend guarantees to the shell

- `GET /health` answers `200 {"status":"ok","version":"<x.y.z>"}` once it can serve.
- It binds `127.0.0.1` only and never opens a different port.
- It **exits with code 2 and writes the reason to stderr** if it detects a server or container and was not given `--data-dir PATH`. The shell passes `HUSK_DATA_DIR` (an env var), and on a server that alone is **not** accepted (by design: the flag must be explicit).
- Exit 255 with `Failed to load Python shared library` means the folder is incomplete (a missing `_internal/`).
- It never reads or writes inside `bin/`; all state is in the data folder (including `connector-secrets.json`, the default connector credential store, and `session-secrets.json`, the login refresh token, both owner-only). `HUSK_SECRET_STORE=keychain` moves both back to the OS keychain (service `ai.husk.desktop`); on macOS an unsigned build is then asked for the login password again after every rebuild and loses the item.
- No AI key and no `.env` are in the bundle.

### State of the shell (re-read Oct 13, 2026, commit `c885613`)

The four changes this document used to recommend are in the shell now:

1. `--data-dir` is passed as an argument as well as `HUSK_DATA_DIR` (`launch_args` in `app.rs`), so a Linux session without a display variable no longer exits 2.
2. `is_intact` requires `_internal/` or a libpython file (`install/installer.rs`).
3. The start-up failure `Failed to load Python shared library` is recognised and routed to "Repair installation" (`process/supervisor.rs`).
4. Downloads are verified against GitHub's SHA-256 `digest` (`install/verify.rs`).

Still open on the shell side: a signed manifest (authenticity), shell self-update, signing secrets, and the CI workflow never having run. See `docs/SHELL_AGENT_BRIEF.md` (local, git-ignored) for the task list.

## 6. What is not in place yet

- No signing or notarisation. A workflow (`.github/workflows/desktop-build.yml`, see [RELEASING.md](RELEASING.md)) now builds, packages, smoke-tests and drafts the release for the three desktop targets, but it has **not been run on GitHub yet** and every asset it produces is unsigned.
- No signed manifest. The shell verifies GitHub's own asset digest, which does not prove who published the release; `SHA256SUMS` is informational.
- Windows and Linux builds have never been produced or started. The macOS arm64 build passes `/health` and the server-guard checks, run from `dist/` (Oct 12, 2026).
- No update flow has been exercised end to end (`alpha-release` → `0.1.x`): the logic exists in the shell (`is_newer`: a real version replaces a non-version tag) but was not run against a real release.

## 7. Single-file option

PyInstaller `--onefile` can produce one executable, but it is not what the plan allows by default.

| | One-folder (current) | One-file |
| --- | --- | --- |
| Start | immediate | extracts ~186 MB to a temp folder on **every** launch (seconds) |
| Antivirus | few false positives | more false positives, especially on Windows |
| Stop | one process | bootloader parent + Python child; a hard kill of the parent can orphan the server and keep port 8765 busy |
| Signing | one signed tree | the outer file only; extracted libraries are unsigned temp files |
| Download | ~186 MB unpacked, smaller as `.tar.gz` | one file, ~70–90 MB (estimate) |

The shell already ships one-folder as **a single downloaded archive**, so users download one file either way. If a one-file build is ever wanted, add an opt-in flag to `installer.py`, then have the shell accept a bare executable asset, and re-test the shutdown path. It is a decision about the plan's antivirus choice.

## What went wrong in the alpha

Oct 12, 2026. The shell tried to start `bin/husk/alpha-release/husk-backend` and the backend exited at once (code 255) with:

```
Failed to load Python shared library '…/bin/husk/alpha-release/_internal/libpython3.11.dylib': … (no such file)
```

- **Cause:** that folder held only the executable (byte-identical to the one built here, sha256 `c742f76c…`) and a hidden empty `.bare-executable` file. `_internal/` was absent.
- **Why the shell did not notice:** `is_intact` returned true (executable present, and the folder had more than one entry because of the hidden marker), so no repair was offered; the supervisor just retried the start.
- **What wrote the executable alone:** the GitHub release itself. `alpha-release` (title `husk-ai-valpha`, published Oct 11, 2026) has exactly one asset, named `husk-backend`, 26,490,640 bytes, sha256 `c742f76c…`: the bare executable, uploaded by hand without `_internal/` and without the `<prefix>-<version>-<triple>.tar.gz` name. The `.bare-executable` marker is not in this repository or in the shell Rust sources I read, so whichever shell build installed it handled a bare-file asset in a way I could not find in that source.
- **Local fix applied:** `dist/husk-backend/_internal` was copied next to the executable; the binary then started and answered `/health`. This is a stopgap: a shell re-install of `alpha-release` can overwrite it.
- **Permanent fix:** publish the archive described in §2 (the whole folder), and apply recommendations 2 and 3 in §5.

## Release checklist

1. `uv run pytest`, `(cd app/frontend && npm test)`, `ruff` all green.
2. `uv run python installer.py --smoke` on each target OS.
3. Sign and notarise the folder.
4. Archive the whole `husk-backend/` folder as `husk-backend-<version>-<triple>.tar.gz` (or `.zip` on Windows).
5. Run the archive check in §2 (executable and `_internal/libpython*` present).
6. Publish the GitHub release with the tag, the assets and `SHA256SUMS`.
7. On a clean profile: let the shell install it, confirm `logs/husk.log` shows `Husk <version> ready on http://127.0.0.1:8765`, then stop the shell and confirm no `husk-backend` process remains.
