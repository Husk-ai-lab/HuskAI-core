"""Package the PyInstaller build as release archives and describe them (CI and one machine alike).

    python scripts/release.py version [--ref-type tag --ref-name v0.1.0-alpha.1]
    python scripts/release.py package --version 0.1.0-alpha.1 [--triple T] [--smoke]
    python scripts/release.py report  [--tag v0.1.0-alpha.1] [--require T1,T2]
    python scripts/release.py build   --targets mac-arm64,mac-x64    # one machine, no CI

`package` takes the one-folder build in dist/husk-backend/ and produces the asset the desktop shell
downloads (husk-backend-<version>-<triple>.tar.gz | .zip), after checking the folder and the archive
and (with --smoke) running the executable out of the extracted archive the way the shell does.
`report` merges the per-target fragments into release-report.json (a stable schema other services can
read), RELEASE_REPORT.md and SHA256SUMS. Nothing here signs: signing happens before `package`, in CI.
Standard library only, so it runs before the project's dependencies are installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "husk-backend"
SCHEMA = 1
SHELL_READY_SECONDS = 20  # how long the desktop shell polls /health before it gives up
# The triples the desktop shell knows how to ask for (huskai-desktop `platform.rs`).
TRIPLES = ("aarch64-apple-darwin", "x86_64-apple-darwin", "x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu")
# Loadable native code is checked for the right CPU; bundled launcher .exe files (setuptools ships
# 32-bit and arm64 ones) are data, not code this build runs.
NATIVE_SUFFIXES = {".dylib", ".pyd", ".dll"}
# The Python runtime PyInstaller puts in _internal/: libpython3.x.dylib/.so (python-build-standalone, uv,
# Linux), python3xx.dll (Windows) or plain `Python` (a macOS framework build such as the hosted runners'
# and python.org installers).
LIBPYTHON = ("libpython3*", "python3*.dll", "Python")
LIBPYTHON_ARCHIVE = re.compile(rf"{NAME}/_internal/(libpython3.*|python3\d*\.dll|Python)")
JUNK_NAMES = {".env", ".DS_Store", ".bare-executable"}
CPU = {"macho": {0x0100000C: "arm64", 0x01000007: "x86_64"},
       "pe": {0x8664: "x86_64", 0xAA64: "arm64", 0x014C: "x86"},
       "elf": {0x3E: "x86_64", 0xB7: "arm64", 0x03: "x86"}}


# ---------------------------------------------------------------- identity

def base_version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "app" / "__init__.py").read_text("utf-8"))
    if not m:
        sys.exit("cannot read __version__ from app/__init__.py")
    return m.group(1)


def release_version(base: str, ref_type: str | None, ref_name: str | None, sha: str | None) -> str:
    """The version label of this build: the tag's, or `<base>-dev.<sha>` for anything else."""
    if ref_type == "tag" and ref_name:
        v = ref_name.removeprefix("v")
        if v != base and not v.startswith(base + "-"):
            sys.exit(f"tag {ref_name} does not match the app version {base} (expected v{base} or v{base}-<suffix>)")
        return v
    return f"{base}-dev.{(sha or 'local')[:7]}"


def channel_for(version: str) -> str:
    return "beta" if "-" in version else "stable"


def host_triple() -> str:
    machine = platform.machine().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(machine, machine)
    system = {"Darwin": "apple-darwin", "Windows": "pc-windows-msvc", "Linux": "unknown-linux-gnu"}.get(platform.system())
    if not system:
        sys.exit(f"unsupported operating system: {platform.system()}")
    return f"{arch}-{system}"


def exe_name(triple: str) -> str:
    return NAME + (".exe" if "windows" in triple else "")


def archive_name(version: str, triple: str) -> str:
    return f"{NAME}-{version}-{triple}." + ("zip" if "windows" in triple else "tar.gz")


def expected_arch(triple: str) -> str:
    return "arm64" if triple.startswith("aarch64") else "x86_64"


def is_emulated(flag: bool) -> bool:
    """True when this build ran through an emulator (Rosetta, Windows-on-ARM), so it is not proof of a
    real machine of that architecture."""
    if flag:
        return True
    if platform.system() == "Darwin":
        r = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True)
        return r.stdout.strip() == "1"
    return False


# ---------------------------------------------------------------- inspecting binaries

def binary_archs(path: Path) -> set[str] | None:
    """CPU architectures inside a Mach-O (thin or fat), PE or ELF file; None when it is none of them."""
    try:
        with path.open("rb") as f:
            head = f.read(512)
            if len(head) < 8:
                return None
            magic_le, magic_be = struct.unpack("<I", head[:4])[0], struct.unpack(">I", head[:4])[0]
            if magic_le in (0xFEEDFACF, 0xFEEDFACE):
                return {CPU["macho"].get(struct.unpack("<I", head[4:8])[0], "other")}
            if magic_be in (0xCAFEBABE, 0xCAFEBABF):
                n, stride = struct.unpack(">I", head[4:8])[0], (32 if magic_be == 0xCAFEBABF else 20)
                if not 0 < n <= 8:  # a Java class file shares the magic; its "count" is a version
                    return None
                return {CPU["macho"].get(struct.unpack(">I", head[8 + i * stride:12 + i * stride])[0], "other")
                        for i in range(n)}
            if head[:4] == b"\x7fELF":
                return {CPU["elf"].get(struct.unpack("<H", head[18:20])[0], "other")}
            if head[:2] == b"MZ" and len(head) >= 0x40:
                f.seek(struct.unpack("<I", head[0x3C:0x40])[0])
                pe = f.read(6)
                if pe[:4] == b"PE\0\0" and len(pe) == 6:
                    return {CPU["pe"].get(struct.unpack("<H", pe[4:6])[0], "other")}
    except OSError:
        return None
    return None


def check_folder(folder: Path, triple: str) -> tuple[list[str], int]:
    """Problems that would make the shell install broken, plus how many native files were arch-checked."""
    problems: list[str] = []
    exe, internal = folder / exe_name(triple), folder / "_internal"
    if not exe.is_file():
        problems.append(f"{exe.name} is missing from {folder}")
    if not internal.is_dir():
        problems.append("_internal/ is missing: the executable cannot start without it")
    elif not any(f.is_file() for pat in LIBPYTHON for f in internal.glob(pat)):
        problems.append("_internal/ holds no libpython (the Python runtime)")
    want, wrong, checked = expected_arch(triple), [], 0
    for p in sorted(folder.rglob("*")) if folder.is_dir() else []:
        if p.name.startswith("._") or p.name in JUNK_NAMES:
            problems.append(f"unwanted file in the bundle: {p.relative_to(folder)}")
        if p.is_symlink() or not p.is_file():
            continue
        if p == exe or p.suffix in NATIVE_SUFFIXES or ".so" in p.name or p.name == "Python":
            archs = binary_archs(p)
            if archs is not None:
                checked += 1
                if want not in archs:
                    wrong.append(f"{p.relative_to(folder)} ({'/'.join(sorted(archs))})")
    if wrong:
        problems.append(f"{len(wrong)} native file(s) are not {want}: " + ", ".join(wrong[:5]))
    return problems, checked


# ---------------------------------------------------------------- the archive

def _anonymous(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def make_archive(folder: Path, dest: Path) -> None:
    """The whole folder under one top directory. tarfile does not add macOS AppleDouble files."""
    if dest.suffix == ".zip":
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(folder.rglob("*")):
                z.write(p, f"{NAME}/{p.relative_to(folder).as_posix()}")
    else:
        with tarfile.open(dest, "w:gz") as t:
            t.add(folder, arcname=NAME, filter=_anonymous)  # symlinks stay symlinks, owner names are dropped


def inspect_archive(archive: Path, triple: str) -> tuple[list[str], int, int]:
    """Check what is actually shipped: (problems, entries, unpacked bytes)."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            members = [(i.filename, i.file_size, None) for i in z.infolist()]
    else:
        with tarfile.open(archive) as t:
            members = [(m.name, m.size, m.mode) for m in t.getmembers()]
    problems: list[str] = []
    names = [n for n, _, _ in members]
    if any(n.startswith("/") or ".." in Path(n).parts for n in names):
        problems.append("the archive has absolute or parent-relative paths (the shell rejects it)")
    if any(not (n == NAME or n.startswith(NAME + "/")) for n in names):
        problems.append(f"every entry must be under a single top folder `{NAME}/`")
    for need in (f"{NAME}/{exe_name(triple)}", f"{NAME}/_internal"):
        if not any(n.rstrip("/") == need or n.startswith(need + "/") for n in names):
            problems.append(f"the archive has no {need}")
    if not any(LIBPYTHON_ARCHIVE.fullmatch(n) for n in names):
        problems.append("the archive has no libpython")
    if any(Path(n).name.startswith("._") or Path(n).name in JUNK_NAMES for n in names):
        problems.append("the archive has AppleDouble or junk files")
    exe = next((m for m in members if m[0] == f"{NAME}/{exe_name(triple)}"), None)
    if exe and exe[2] is not None and not exe[2] & 0o111:
        problems.append("the executable has no execute bit")
    return problems, len(members), sum(s for _, s, _ in members)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(archive: Path, dest: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            if hasattr(tarfile, "data_filter"):
                t.extractall(dest, filter="data")  # rejects absolute and escaping links, like the shell
            else:
                t.extractall(dest)


# ---------------------------------------------------------------- smoke test

def smoke(archive: Path, triple: str, *, data_dir_flag: bool = False, timeout: float = 60) -> dict:
    """Run the executable out of the extracted archive the way the shell does (env vars, no arguments)."""
    started = time.monotonic()
    result: dict = {"passed": False, "health_version": None, "seconds": None, "detail": None}
    with tempfile.TemporaryDirectory(prefix="husk-smoke-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        extract(archive, root / "bin")
        folder, data, log = root / "bin" / NAME, root / "data", root / "husk.log"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {**os.environ, "HUSK_PORT": str(port), "HUSK_DATA_DIR": str(data), "HUSK_ENV": "prod",
               "HUSK_LAUNCH_SECRET": secrets.token_hex(32)}
        cmd = [str(folder / exe_name(triple))] + (["--data-dir", str(data)] if data_dir_flag else [])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with log.open("wb") as out:
            proc = subprocess.Popen(cmd, cwd=folder, env=env, stdout=out, stderr=subprocess.STDOUT)
        try:
            while time.monotonic() - started < timeout:
                if proc.poll() is not None:
                    tail = log.read_text("utf-8", "replace").strip().splitlines()[-6:]
                    result["detail"] = f"exited with code {proc.returncode}: " + " | ".join(tail)
                    break
                try:
                    with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                        body = json.loads(r.read().decode())
                    if body.get("status") == "ok":
                        result.update(passed=True, health_version=body.get("version"))
                        break
                except (OSError, ValueError):
                    time.sleep(0.5)
            else:
                result["detail"] = f"/health did not answer within {timeout:.0f} s"
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


# ---------------------------------------------------------------- package

def cmd_package(a: argparse.Namespace) -> int:
    triple = a.triple or host_triple()
    if triple not in TRIPLES:
        sys.exit(f"unknown target triple {triple}; the shell asks for one of: {', '.join(TRIPLES)}")
    folder, out = Path(a.folder), Path(a.out)
    (out / "report").mkdir(parents=True, exist_ok=True)
    name = archive_name(a.version, triple)
    try:
        from importlib.metadata import version as dist_version
        pyinstaller = dist_version("pyinstaller")
    except Exception:  # noqa: BLE001 - informational only
        pyinstaller = None
    fragment: dict = {
        "triple": triple, "version": a.version, "status": "failed", "asset_name": name, "size_bytes": None,
        "sha256": None, "unpacked_bytes": None, "entries": None, "python": platform.python_version(),
        "pyinstaller": pyinstaller, "arch_check": {"expected": expected_arch(triple), "native_files": 0},
        "smoke": None, "signed": {"kind": a.signed, "notarised": a.notarised},
        "verified_on_real_hardware": not is_emulated(a.emulated), "problems": [],
    }
    problems, native = check_folder(folder, triple)
    fragment["arch_check"]["native_files"] = native
    archive = out / name
    if not problems:
        make_archive(folder, archive)
        problems, entries, unpacked = inspect_archive(archive, triple)
        fragment.update(size_bytes=archive.stat().st_size, sha256=sha256(archive), entries=entries,
                        unpacked_bytes=unpacked)
    if not problems and a.smoke:
        fragment["smoke"] = smoke(archive, triple, data_dir_flag=a.smoke_data_dir_flag)
        if not fragment["smoke"]["passed"]:
            problems.append("smoke test failed: " + str(fragment["smoke"]["detail"]))
    fragment["problems"] = problems
    fragment["status"] = "failed" if problems else "built"
    (out / "report" / f"{triple}.json").write_text(json.dumps(fragment, indent=2) + "\n", "utf-8")
    if problems:
        print(f"{triple}: FAILED", *problems, sep="\n  - ", file=sys.stderr)
        return 1
    print(f"{triple}: {name} {fragment['size_bytes'] / 1e6:.1f} MB sha256 {fragment['sha256'][:16]}… "
          f"({native} native files are {expected_arch(triple)}"
          + (f", /health {fragment['smoke']['health_version']} in {fragment['smoke']['seconds']} s" if a.smoke else "")
          + ")")
    return 0


# ---------------------------------------------------------------- report

def junit_summary(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return {"passed": total - failed - skipped, "failed": failed, "skipped": skipped}


def vitest_summary(path: Path) -> dict:
    d = json.loads(path.read_text("utf-8"))
    return {"passed": d.get("numPassedTests", 0), "failed": d.get("numFailedTests", 0),
            "skipped": d.get("numPendingTests", 0)}


def git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def build_report(fragments: list[dict], *, tag: str | None, quality: dict, min_version: str | None,
                 required: list[str], env: dict[str, str] | None = None) -> dict:
    env = dict(os.environ) if env is None else env
    versions = {f["version"] for f in fragments}
    if len(versions) > 1:
        sys.exit(f"the targets were built with different versions: {sorted(versions)}")
    version = versions.pop() if versions else base_version()
    if tag and tag.removeprefix("v") != version:
        sys.exit(f"tag {tag} does not match the packaged version {version}")
    repo, run_id = env.get("GITHUB_REPOSITORY"), env.get("GITHUB_RUN_ID")
    built = {f["triple"]: f for f in fragments if f["status"] == "built"}
    runner = env.get("RUNNER_ENVIRONMENT")
    ref_type = env.get("GITHUB_REF_TYPE")
    gaps: list[str] = []
    for triple in TRIPLES:
        if triple not in built:
            gaps.append(f"no build for {triple}")
    if unsigned := [t for t, f in built.items() if f["signed"]["kind"] == "none"]:
        gaps.append("not code-signed: " + ", ".join(unsigned))
    if emulated := [t for t, f in built.items() if not f["verified_on_real_hardware"]]:
        gaps.append("only run under emulation, never on a real machine of that CPU: " + ", ".join(emulated))
    if slow := [t for t, f in built.items() if f["smoke"] and f["smoke"]["seconds"] > SHELL_READY_SECONDS]:
        gaps.append(f"start-up took longer than the shell's {SHELL_READY_SECONDS} s readiness wait: " + ", ".join(slow))
    gaps.append("the desktop shell has not been shown installing and starting these assets end to end")
    return {
        "schema": SCHEMA, "component": "husk", "version": version, "tag": tag,
        "commit": env.get("GITHUB_SHA") or git("rev-parse", "HEAD"),
        "branch": env.get("GITHUB_REF_NAME") if ref_type == "branch" else git("rev-parse", "--abbrev-ref", "HEAD"),
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "channel": channel_for(version), "min_version": min_version,
        "builder": {"kind": runner or "local", "os": platform.system(), "arch": platform.machine(),
                    "run_url": f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}"
                    if repo and run_id else None},
        "quality": quality,
        "required": required,
        "targets": sorted(fragments, key=lambda f: f["triple"]),
        # Shaped like the `assets` column of public.husk_releases.
        "assets": [{"triple": t, "name": f["asset_name"], "size": f["size_bytes"], "sha256": f["sha256"],
                    "url": f"https://github.com/{repo}/releases/download/{tag}/{f['asset_name']}" if repo and tag else None}
                   for t, f in sorted(built.items())],
        "known_gaps": gaps,
    }


def render_markdown(r: dict) -> str:
    L = [f"# Husk backend {r['version']}", "",
         f"Commit `{(r['commit'] or 'unknown')[:10]}` · {r['built_at']} · channel **{r['channel']}**"
         + (f" · [run]({r['builder']['run_url']})" if r["builder"]["run_url"] else "")
         + f" · built on {r['builder']['kind']} ({r['builder']['os']} {r['builder']['arch']})", "",
         "| Target | Result | Asset | Size | Smoke | Signed |", "| --- | --- | --- | --- | --- | --- |"]
    for t in r["targets"]:
        s = t["smoke"]
        size = f"{t['size_bytes'] / 1e6:.1f} MB" if t["size_bytes"] else "—"
        smoke_cell = f"ok, {s['seconds']} s" if s and s["passed"] else ("failed" if s else "not run")
        asset = t["asset_name"] if t["status"] == "built" else "—"
        L.append(f"| `{t['triple']}` | {t['status']} | {asset} | {size} | {smoke_cell} | {t['signed']['kind']} |")
    if r["quality"]:
        L += ["", "## Tests", ""]
        L += [f"- {k}: " + (", ".join(f"{n} {v}" for v, n in d.items()) if isinstance(d, dict) else str(d))
              for k, d in r["quality"].items()]
    failed = [t for t in r["targets"] if t["problems"]]
    if failed:
        L += ["", "## Problems", ""] + [f"- `{t['triple']}`: {p}" for t in failed for p in t["problems"]]
    L += ["", "## Known gaps", ""] + [f"- {g}" for g in r["known_gaps"]]
    L += ["", "## Checksums (SHA-256)", ""] + [f"- `{a['sha256']}`  {a['name']}" for a in r["assets"]]
    return "\n".join(L) + "\n"


def cmd_report(a: argparse.Namespace) -> int:
    reports, out = Path(a.reports), Path(a.out)
    fragments = [json.loads(p.read_text("utf-8")) for p in sorted(reports.glob("*.json"))]
    quality: dict = {}
    if a.pytest_xml and Path(a.pytest_xml).is_file():
        quality["pytest"] = junit_summary(Path(a.pytest_xml))
    if a.vitest_json and Path(a.vitest_json).is_file():
        quality["vitest"] = vitest_summary(Path(a.vitest_json))
    for item in a.check:
        k, _, v = item.partition("=")
        quality[k] = v
    required = [t for t in a.require.split(",") if t]
    report = build_report(fragments, tag=a.tag, quality=quality, min_version=a.min_version, required=required)
    out.mkdir(parents=True, exist_ok=True)
    (out / "release-report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    (out / "RELEASE_REPORT.md").write_text(render_markdown(report), "utf-8")
    (out / "SHA256SUMS").write_text("".join(f"{x['sha256']}  {x['name']}\n" for x in report["assets"]), "utf-8")
    print(f"wrote {out / 'release-report.json'} ({len(report['assets'])} asset(s))")
    missing = [t for t in required if t not in {x["triple"] for x in report["assets"]}]
    if missing:
        print("required target(s) not built: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- one machine (no CI)

LOCAL_TARGETS = {
    "mac-arm64": {"triple": "aarch64-apple-darwin", "venv": ROOT / ".venv", "python": None},
    # Rosetta runs an x86_64 interpreter transparently; uv itself stays arm64.
    "mac-x64": {"triple": "x86_64-apple-darwin", "venv": ROOT / ".venv-x64", "python": "cpython-3.11-macos-x86_64"},
}


def cmd_build(a: argparse.Namespace) -> int:
    wanted = [t for t in a.targets.split(",") if t]
    unknown = [t for t in wanted if t not in LOCAL_TARGETS]
    if unknown:
        sys.exit(f"unknown target(s) {unknown}; this machine can build: {', '.join(LOCAL_TARGETS)}. "
                 "Windows needs a Windows machine or VM (PyInstaller cannot cross-compile).")
    if platform.system() != "Darwin":
        sys.exit("the local driver builds the macOS targets and must run on a Mac")
    if "mac-x64" in wanted and subprocess.run(["arch", "-x86_64", "/usr/bin/true"], capture_output=True).returncode:
        sys.exit("Rosetta 2 is not installed: run `softwareupdate --install-rosetta --agree-to-license` once")
    version = a.version or release_version(base_version(), None, None, git("rev-parse", "HEAD"))
    out = Path(a.out)
    shutil.rmtree(out / "report", ignore_errors=True)
    frontend = ROOT / "app" / "frontend"
    if a.rebuild_ui or not (frontend / "dist" / "index.html").exists():
        subprocess.run(["npm", "ci"], cwd=frontend, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend, check=True)
    failures = 0
    for key in wanted:
        t = LOCAL_TARGETS[key]
        py = ["--python", t["python"]] if t["python"] else []
        env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(t["venv"])}
        if t["python"]:
            subprocess.run(["uv", "python", "install", t["python"]], check=True, env=env)
        subprocess.run(["uv", "sync", "--frozen", *py], check=True, cwd=ROOT, env=env)
        venv_python = str(t["venv"] / "bin" / "python")
        subprocess.run([venv_python, "installer.py", "--skip-ui"], check=True, cwd=ROOT)
        failures += subprocess.run([venv_python, "scripts/release.py", "package", "--version", version,
                                    "--triple", t["triple"], "--smoke", "--out", str(out)], cwd=ROOT).returncode != 0
    report = [sys.executable, "scripts/release.py", "report", "--reports", str(out / "report"), "--out", str(out),
              "--require", ",".join(LOCAL_TARGETS[k]["triple"] for k in wanted)]
    failures += subprocess.run(report, cwd=ROOT).returncode != 0
    return 1 if failures else 0


# ---------------------------------------------------------------- entry point

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("version", help="print version=<label> for this build (for $GITHUB_OUTPUT)")
    v.add_argument("--ref-type"), v.add_argument("--ref-name"), v.add_argument("--sha")

    p = sub.add_parser("package", help="archive, check and (optionally) smoke-test dist/husk-backend/")
    p.add_argument("--version", required=True)
    p.add_argument("--triple")
    p.add_argument("--folder", default=str(ROOT / "dist" / NAME))
    p.add_argument("--out", default=str(ROOT / "dist"))
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--smoke-data-dir-flag", action="store_true", help="also pass --data-dir (headless Linux)")
    p.add_argument("--signed", choices=["none", "developer-id", "authenticode"], default="none")
    p.add_argument("--notarised", action="store_true")
    p.add_argument("--emulated", action="store_true", help="this build ran under an emulator")

    r = sub.add_parser("report", help="merge per-target results into release-report.json")
    r.add_argument("--reports", default=str(ROOT / "dist" / "report"))
    r.add_argument("--out", default=str(ROOT / "dist"))
    r.add_argument("--tag")
    r.add_argument("--min-version")
    r.add_argument("--require", default="", help="comma-separated triples that must have built")
    r.add_argument("--pytest-xml"), r.add_argument("--vitest-json")
    r.add_argument("--check", action="append", default=[], help="name=outcome, repeatable")

    b = sub.add_parser("build", help="build the macOS targets on this machine, then report")
    b.add_argument("--targets", default="mac-arm64")
    b.add_argument("--version")
    b.add_argument("--out", default=str(ROOT / "dist"))
    b.add_argument("--rebuild-ui", action="store_true")

    a = ap.parse_args(argv)
    if a.cmd == "version":
        print("version=" + release_version(base_version(), a.ref_type, a.ref_name, a.sha))
        return 0
    return {"package": cmd_package, "report": cmd_report, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
