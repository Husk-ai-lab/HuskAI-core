"""The release packaging script (scripts/release.py): what it accepts, what it refuses, what it reports.

Everything runs on synthetic folders: tiny fake Mach-O/PE headers and a stub executable, never the real build.
"""

from __future__ import annotations

import json
import stat
import struct
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path

import pytest

from scripts import release

ARM, X64 = 0x0100000C, 0x01000007
MAC_ARM, MAC_X64 = "aarch64-apple-darwin", "x86_64-apple-darwin"
WIN = "x86_64-pc-windows-msvc"


def macho(cpu: int, size: int = 64) -> bytes:
    return (struct.pack("<II", 0xFEEDFACF, cpu) + b"\0" * size)[:size]


def pe(machine: int) -> bytes:
    head = bytearray(b"MZ" + b"\0" * 0x3E)
    head[0x3C:0x40] = struct.pack("<I", 0x40)
    return bytes(head) + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 16


def build_folder(root: Path, triple: str = MAC_ARM, cpu: int = ARM) -> Path:
    folder = root / "husk-backend"
    internal = folder / "_internal"
    internal.mkdir(parents=True)
    native = pe(0x8664) if "windows" in triple else macho(cpu)
    exe = folder / release.exe_name(triple)
    exe.write_bytes(native)
    exe.chmod(0o755)
    (internal / ("python311.dll" if "windows" in triple else "libpython3.11.dylib")).write_bytes(native)
    (internal / "base_library.zip").write_bytes(b"PK")
    return folder


# ---- identifying binaries

def test_binary_archs_reads_macho_pe_elf_and_ignores_the_rest(tmp_path: Path) -> None:
    files = {
        "arm": macho(ARM), "x64": macho(X64), "win": pe(0x8664), "winarm": pe(0xAA64),
        "elf": b"\x7fELF" + b"\x02\x01\x01" + b"\0" * 11 + struct.pack("<H", 0xB7) + b"\0" * 8,
        "fat": struct.pack(">II", 0xCAFEBABE, 2) + struct.pack(">IIIII", ARM, 0, 0, 0, 0)
        + struct.pack(">IIIII", X64, 0, 0, 0, 0),
        "java": struct.pack(">II", 0xCAFEBABE, 52) + b"\0" * 40,
        "text": b"# just a script\n" * 8,
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    got = {n: release.binary_archs(tmp_path / n) for n in files}
    assert got == {"arm": {"arm64"}, "x64": {"x86_64"}, "win": {"x86_64"}, "winarm": {"arm64"}, "elf": {"arm64"},
                   "fat": {"arm64", "x86_64"}, "java": None, "text": None}


# ---- the folder

def test_a_complete_folder_passes_and_counts_native_files(tmp_path: Path) -> None:
    problems, native = release.check_folder(build_folder(tmp_path), MAC_ARM)
    assert problems == []
    assert native == 2  # the executable and libpython


def test_the_alpha_incident_is_caught(tmp_path: Path) -> None:
    """A folder with the executable but no _internal/ is exactly what broke the first alpha install."""
    folder = tmp_path / "husk-backend"
    folder.mkdir()
    (folder / "husk-backend").write_bytes(macho(ARM))
    problems, _ = release.check_folder(folder, MAC_ARM)
    assert any("_internal/ is missing" in p for p in problems)


def test_a_folder_without_libpython_is_refused(tmp_path: Path) -> None:
    folder = build_folder(tmp_path)
    (folder / "_internal" / "libpython3.11.dylib").unlink()
    problems, _ = release.check_folder(folder, MAC_ARM)
    assert any("libpython" in p for p in problems)


def test_a_wrong_architecture_is_refused_but_launcher_exes_are_not(tmp_path: Path) -> None:
    folder = build_folder(tmp_path, MAC_X64, X64)
    (folder / "_internal" / "libwrong.dylib").write_bytes(macho(ARM))
    problems, _ = release.check_folder(folder, MAC_X64)
    assert any("not x86_64" in p and "libwrong.dylib" in p for p in problems)
    # a setuptools-style 32-bit launcher is data, not code the build runs
    win = build_folder(tmp_path / "w", WIN)
    (win / "_internal" / "cli-32.exe").write_bytes(pe(0x014C))
    assert release.check_folder(win, WIN)[0] == []


@pytest.mark.parametrize("junk", ["._husk-backend", ".env", ".bare-executable", ".DS_Store"])
def test_junk_files_are_refused(tmp_path: Path, junk: str) -> None:
    folder = build_folder(tmp_path)
    (folder / junk).write_bytes(b"x")
    problems, _ = release.check_folder(folder, MAC_ARM)
    assert any("unwanted file" in p for p in problems)


# ---- the archive

def test_tar_archive_roundtrip_keeps_one_top_folder_and_no_owner(tmp_path: Path) -> None:
    folder = build_folder(tmp_path)
    archive = tmp_path / release.archive_name("0.1.0-alpha.1", MAC_ARM)
    release.make_archive(folder, archive)
    assert archive.name == "husk-backend-0.1.0-alpha.1-aarch64-apple-darwin.tar.gz"
    problems, entries, unpacked = release.inspect_archive(archive, MAC_ARM)
    assert problems == []
    assert entries >= 4 and unpacked > 0
    with tarfile.open(archive) as t:
        members = t.getmembers()
    assert {m.name.split("/")[0] for m in members} == {"husk-backend"}
    assert all(m.uid == 0 and m.gid == 0 and m.uname == "" and m.gname == "" for m in members)
    exe = next(m for m in members if m.name == "husk-backend/husk-backend")
    assert exe.mode & stat.S_IXUSR


def test_zip_archive_for_windows(tmp_path: Path) -> None:
    folder = build_folder(tmp_path, WIN)
    archive = tmp_path / release.archive_name("0.1.0", WIN)
    assert archive.name == "husk-backend-0.1.0-x86_64-pc-windows-msvc.zip"
    release.make_archive(folder, archive)
    assert release.inspect_archive(archive, WIN)[0] == []
    with zipfile.ZipFile(archive) as z:
        assert "husk-backend/husk-backend.exe" in z.namelist()


def test_a_bare_executable_archive_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "bare.tar.gz"
    exe = tmp_path / "husk-backend"
    exe.write_bytes(macho(ARM))
    with tarfile.open(archive, "w:gz") as t:
        t.add(exe, arcname="husk-backend")
    problems, _, _ = release.inspect_archive(archive, MAC_ARM)
    assert any("_internal" in p for p in problems) or any("libpython" in p for p in problems)


def test_an_archive_with_escaping_paths_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    src = tmp_path / "f"
    src.write_bytes(b"x")
    with tarfile.open(archive, "w:gz") as t:
        t.add(src, arcname="husk-backend/../evil")
    assert any("parent-relative" in p for p in release.inspect_archive(archive, MAC_ARM)[0])


# ---- running it like the shell

def stub_build(tmp_path: Path, body: str) -> Path:
    """A folder whose 'executable' is a Python script (POSIX only)."""
    folder = build_folder(tmp_path)
    exe = folder / "husk-backend"
    exe.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    exe.chmod(0o755)
    return folder


SERVER = """
    import json, os
    from http.server import BaseHTTPRequestHandler, HTTPServer
    assert os.environ["HUSK_ENV"] == "prod" and len(os.environ["HUSK_LAUNCH_SECRET"]) == 64
    assert os.path.isdir(os.path.dirname(os.environ["HUSK_DATA_DIR"]))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "ok", "version": "9.9.9"}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a): pass
    HTTPServer(("127.0.0.1", int(os.environ["HUSK_PORT"])), H).serve_forever()
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the stub executable is a shebang script")
def test_smoke_runs_the_extracted_archive_with_the_shell_environment(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar.gz"
    release.make_archive(stub_build(tmp_path, SERVER), archive)
    result = release.smoke(archive, MAC_ARM, timeout=20)
    assert result["passed"], result
    assert result["health_version"] == "9.9.9"


@pytest.mark.skipif(sys.platform == "win32", reason="the stub executable is a shebang script")
def test_smoke_reports_an_early_exit(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar.gz"
    release.make_archive(stub_build(tmp_path, "import sys; print('no libpython'); sys.exit(3)"), archive)
    result = release.smoke(archive, MAC_ARM, timeout=20)
    assert not result["passed"]
    assert "code 3" in result["detail"] and "no libpython" in result["detail"]


@pytest.mark.skipif(sys.platform == "win32", reason="the stub executable is a shebang script")
def test_package_command_writes_the_fragment_and_fails_on_a_broken_folder(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_folder(tmp_path / "ok")
    argv = ["package", "--version", "0.1.0-alpha.1", "--triple", MAC_ARM, "--out", str(out)]
    assert release.main([*argv, "--folder", str(tmp_path / "ok" / "husk-backend")]) == 0
    fragment = json.loads((out / "report" / f"{MAC_ARM}.json").read_text())
    assert fragment["status"] == "built" and fragment["sha256"] and fragment["signed"]["kind"] == "none"
    assert (out / fragment["asset_name"]).is_file()

    broken = tmp_path / "broken" / "husk-backend"
    broken.mkdir(parents=True)
    (broken / "husk-backend").write_bytes(macho(ARM))
    assert release.main([*argv, "--folder", str(broken)]) == 1
    assert json.loads((out / "report" / f"{MAC_ARM}.json").read_text())["status"] == "failed"


# ---- versions, tags and the report

def test_release_version_follows_the_tag_or_is_a_dev_build() -> None:
    assert release.release_version("0.1.0", "tag", "v0.1.0-alpha.1", "abcdef1234") == "0.1.0-alpha.1"
    assert release.release_version("0.1.0", "tag", "v0.1.0", None) == "0.1.0"
    assert release.release_version("0.1.0", "branch", "main", "abcdef1234") == "0.1.0-dev.abcdef1"
    with pytest.raises(SystemExit):
        release.release_version("0.1.0", "tag", "v0.2.0", None)
    assert release.channel_for("0.1.0-alpha.1") == "beta"
    assert release.channel_for("0.1.0") == "stable"


def fragment(triple: str, **over) -> dict:
    base = {"triple": triple, "version": "0.1.0-alpha.1", "status": "built",
            "asset_name": release.archive_name("0.1.0-alpha.1", triple), "size_bytes": 1000, "sha256": "ab" * 32,
            "unpacked_bytes": 2000, "entries": 9, "python": "3.11.15", "pyinstaller": "6.22",
            "arch_check": {"expected": "arm64", "native_files": 2},
            "smoke": {"passed": True, "health_version": "0.1.0", "seconds": 3.1, "detail": None},
            "signed": {"kind": "none", "notarised": False}, "verified_on_real_hardware": True, "problems": []}
    return {**base, **over}


ENV = {"GITHUB_REPOSITORY": "Husk-ai-lab/HuskAI-core", "GITHUB_RUN_ID": "42", "GITHUB_SHA": "c0ffee" * 7,
       "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.1.0-alpha.1", "RUNNER_ENVIRONMENT": "github-hosted"}


def test_report_lists_assets_gaps_and_the_husk_releases_shape() -> None:
    r = release.build_report([fragment(MAC_ARM), fragment(MAC_X64, verified_on_real_hardware=False)],
                             tag="v0.1.0-alpha.1", quality={"pytest": {"passed": 5, "failed": 0, "skipped": 0}},
                             min_version=None, required=[MAC_ARM], env=ENV)
    assert r["schema"] == release.SCHEMA and r["component"] == "husk" and r["channel"] == "beta"
    assert r["builder"]["kind"] == "github-hosted"
    assert r["builder"]["run_url"] == "https://github.com/Husk-ai-lab/HuskAI-core/actions/runs/42"
    assert [a["triple"] for a in r["assets"]] == [MAC_ARM, MAC_X64]
    assert r["assets"][0]["url"].endswith("/v0.1.0-alpha.1/husk-backend-0.1.0-alpha.1-aarch64-apple-darwin.tar.gz")
    assert set(r["assets"][0]) == {"triple", "name", "size", "sha256", "url"}
    gaps = " ".join(r["known_gaps"])
    assert "no build for x86_64-pc-windows-msvc" in gaps and "not code-signed" in gaps
    assert "emulation" in gaps and MAC_X64 in gaps


def test_report_flags_a_start_slower_than_the_shell_waits() -> None:
    slow = fragment(MAC_ARM, smoke={"passed": True, "health_version": "0.1.0", "seconds": 21.5, "detail": None})
    r = release.build_report([slow], tag=None, quality={}, min_version=None, required=[], env={})
    assert any("readiness wait" in g and MAC_ARM in g for g in r["known_gaps"])
    fast = release.build_report([fragment(MAC_ARM)], tag=None, quality={}, min_version=None, required=[], env={})
    assert not any("readiness wait" in g for g in fast["known_gaps"])


def test_report_refuses_a_tag_that_does_not_match_the_build() -> None:
    with pytest.raises(SystemExit):
        release.build_report([fragment(MAC_ARM)], tag="v0.2.0", quality={}, min_version=None, required=[], env=ENV)
    with pytest.raises(SystemExit):
        release.build_report([fragment(MAC_ARM), fragment(MAC_X64, version="0.1.1")], tag=None, quality={},
                             min_version=None, required=[], env=ENV)


def test_report_command_writes_the_files_and_fails_when_a_required_target_is_missing(tmp_path: Path) -> None:
    reports = tmp_path / "report"
    reports.mkdir()
    (reports / f"{MAC_ARM}.json").write_text(json.dumps(fragment(MAC_ARM)))
    (reports / f"{WIN}.json").write_text(json.dumps(fragment(WIN, status="failed", sha256=None, size_bytes=None,
                                                             problems=["smoke test failed: boom"])))
    junit = tmp_path / "pytest.xml"
    junit.write_text('<testsuites><testsuite tests="10" failures="1" errors="0" skipped="2"/></testsuites>')
    common = ["report", "--reports", str(reports), "--out", str(tmp_path), "--pytest-xml", str(junit),
              "--check", "verify=success"]
    assert release.main([*common, "--require", MAC_ARM]) == 0
    assert release.main([*common, "--require", f"{MAC_ARM},{WIN}"]) == 1
    data = json.loads((tmp_path / "release-report.json").read_text())
    assert data["quality"]["pytest"] == {"passed": 7, "failed": 1, "skipped": 2}
    assert data["quality"]["verify"] == "success"
    assert (tmp_path / "SHA256SUMS").read_text() == f"{'ab' * 32}  husk-backend-0.1.0-alpha.1-{MAC_ARM}.tar.gz\n"
    md = (tmp_path / "RELEASE_REPORT.md").read_text()
    assert "smoke test failed: boom" in md and "Known gaps" in md
