# Releasing the Husk backend binary

Oct 13, 2026 · @Marco · alpha, non-production

How a commit becomes the archives the desktop shell downloads, and how the same steps run on one machine without CI. The shell's side of the contract (asset names, layout, start-up) is in [DESKTOP_SHELL_INTEGRATION.md](DESKTOP_SHELL_INTEGRATION.md).

**Status:** `scripts/release.py` and its tests are verified locally (macOS arm64 build packaged and smoke-tested). The GitHub workflow has **not run yet**: no workflow can be run from here, and the Intel Mac and Windows jobs have never produced a build. Expect a fix iteration or two on those.

## What runs

| Trigger | Result |
| --- | --- |
| push to `main` touching `app/`, `installer.py`, `pyproject.toml`, `uv.lock`, `scripts/` | tests, build, package, smoke, workflow artefacts + report. **No release** |
| tag `v<app version>[-suffix]` | the same, then a **draft** GitHub release (prerelease when the tag has a suffix) |
| manual (`workflow_dispatch`) | same as main, label `<version>-dev.<sha>` |

There is deliberately no `pull_request` trigger (the repository is public) and the release is a draft: a person reads the report and publishes. The shell's stable channel reads `/releases/latest`, so publishing is what reaches users.

Jobs: `verify` (ruff, pytest, vitest, UI build, once on Linux) → `build` matrix (`mac-arm64`, `mac-x64`, `win-x64`; each reuses the built UI and runs `installer.py --skip-ui`, then `scripts/release.py package --smoke`) → `report` → `release` (tags only).

`REQUIRED_TRIPLES` at the top of the workflow decides which targets must build. It starts at `aarch64-apple-darwin`; the Intel and Windows jobs are `continue-on-error` until they have gone green once, then add their triples there and drop `experimental`.

## Cutting a release

1. Set `app/__init__.py` and `pyproject.toml` to the version (they must agree), commit to `main`, wait for the green run.
2. `git tag v0.1.0-alpha.1 && git push origin v0.1.0-alpha.1`. The tag must be `v<app version>` or `v<app version>-<suffix>`; anything else fails in `scripts/release.py`.
3. Open the draft release, read the report in its notes (known gaps are listed), publish.
4. On a clean profile let the shell install it and check `logs/husk.log` for `Husk <version> ready`. This step is **not** automated and has never been run against a published release.

## The report

Every run writes `release-report.json` (stable `schema`, uploaded as an artefact and attached to releases), `RELEASE_REPORT.md` (the job summary and the release notes) and `SHA256SUMS`.

| Field | Meaning |
| --- | --- |
| `component`, `version`, `channel`, `min_version`, `commit`, `tag`, `built_at` | identity; `channel` is `beta` when the version has a suffix |
| `builder` | `github-hosted`, `self-hosted` or `local`, OS/arch, run URL |
| `quality` | pytest and vitest counts, the verify job's result |
| `targets[]` | per triple: status, asset, size, sha256, entries, python/pyinstaller versions, arch check, smoke (`/health` version, seconds), signing, `verified_on_real_hardware` (false under Rosetta or other emulation), problems |
| `assets[]` | `{triple, name, size, sha256, url}`, shaped like the `assets` column of `public.husk_releases` |
| `known_gaps[]` | what the build does **not** prove: unsigned, emulated only, missing targets, start-up slower than the shell's 20 s wait, shell install not exercised |

Other services read the public release or the artefact. CI never writes Neon (hard rule 1) and nothing here couples to Husk Studio (hard rule 2): whoever owns `ops.releases` ingests `version`, `channel` and `assets` from this file.

## One machine, no CI

```bash
uv run python scripts/release.py build --targets mac-arm64            # native
uv run python scripts/release.py build --targets mac-arm64,mac-x64    # + Intel under Rosetta
```

- It builds the UI once, then per target creates its own venv (`.venv`, `.venv-x64`), runs `installer.py --skip-ui`, packages, smoke-tests, and finally writes the report to `dist/`.
- `mac-x64` needs Rosetta 2 once (`softwareupdate --install-rosetta --agree-to-license`) and an x86_64 CPython that `uv` fetches. Rosetta runs an x86_64 interpreter transparently, so `uv` itself stays arm64. The result is checked to be x86_64 only, but it ran under emulation: the report says so. **Not run yet** (Rosetta is not installed on the development Mac).
- Windows cannot be built on a Mac with PyInstaller (no cross-compilation). Use a Windows machine or VM and run `scripts/release.py package` there, or rely on the hosted runner. Wine images are not recommended for something shipped.
- `scripts/release.py package` on its own re-packages an existing `dist/husk-backend/`.

## Self-hosted runner (optional)

Hosted runners are free for a public repository, so a self-hosted Mac only makes sense for speed or offline work. If you add one: change `runs-on` for `mac-arm64`, keep the `push`/`tag`/`dispatch` triggers only (a fork PR would run its code on your machine), require approval for outside collaborators, use a dedicated macOS user, and never keep signing secrets on a runner shared with untrusted jobs.

## Not in place yet

- Code signing and notarisation (macOS Developer ID, Windows Authenticode). No certificates exist; every asset is unsigned. When they do, signing goes **before** `package` in the `build` job, gated on the secrets being present, and `--signed developer-id --notarised` records it. Signed builds also keep a stable keychain identity, which is the gate for moving the connector secrets back to the keychain (see [CONNECTORS.md](CONNECTORS.md)).
- A Linux asset (`x86_64-unknown-linux-gnu`, the shell supports it): add a matrix row on `ubuntu-22.04` with `--smoke-data-dir-flag` (a headless Linux counts as a server and needs `--data-dir`).
- Shell-side verification of `SHA256SUMS` (the shell uses `NoopVerifier`).
