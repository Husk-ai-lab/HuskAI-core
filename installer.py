"""Build the Husk backend as a desktop binary with PyInstaller.

    uv run python installer.py              # build UI + binary into dist/husk-backend/
    uv run python installer.py --skip-ui    # reuse an existing app/frontend/dist
    uv run python installer.py --smoke      # also start the binary and check /health

Choices from the technical plan's antivirus section, applied on every build:
  * one-folder mode, never one-file (no self-extraction to a temp folder)
  * no UPX compression
  * Windows version metadata (company, product, version)
Code signing / notarisation and the rebuilt bootloader happen in CI (GitHub Actions secrets),
never on a laptop. The `.env` file is NOT bundled: no paid secret ships inside the binary.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "app" / "frontend"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "husk-backend"
SEP = ";" if os.name == "nt" else ":"

# Packages that ship data files or plugins (models, tokenizers, entry points). Everything else,
# including mcp and langchain_*, is found by PyInstaller's static import analysis.
COLLECT_ALL = ["markitdown", "magika", "bm25s", "Stemmer", "tiktoken", "tiktoken_ext", "openinference", "pdfminer"]
EXCLUDE = ["mcp.cli", "tkinter", "matplotlib", "IPython", "pytest"]
HIDDEN = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
          "uvicorn.lifespan.on", "keyring.backends", "mcp.client.streamable_http", "langchain_openai", "apscheduler.triggers.cron"]

ENTRY = '''import multiprocessing
multiprocessing.freeze_support()
from app.main import run
run()
'''


def version() -> str:
    from app import __version__

    return __version__


def build_ui() -> None:
    npm = shutil.which("npm") or sys.exit("npm non trovato: installa Node.js 20+")
    subprocess.run([npm, "ci" if (FRONTEND / "package-lock.json").exists() else "install"], cwd=FRONTEND, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True)


def windows_version_file(v: str) -> Path:
    parts = (v.split("-")[0].split(".") + ["0", "0", "0"])[:4]
    tup = ", ".join(parts)
    f = BUILD / "version_info.txt"
    f.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Husk AI'), StringStruct('FileDescription', 'Husk backend'),
    StringStruct('FileVersion', '{v}'), StringStruct('InternalName', '{NAME}'),
    StringStruct('LegalCopyright', 'Husk AI - Business Source License 1.1'), StringStruct('OriginalFilename', '{NAME}.exe'),
    StringStruct('ProductName', 'Husk'), StringStruct('ProductVersion', '{v}')])]),
  VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", "utf-8")
    return f


def build_binary() -> Path:
    if not (FRONTEND / "dist" / "index.html").exists():
        sys.exit("UI non compilata: esegui senza --skip-ui")
    BUILD.mkdir(exist_ok=True)
    entry = BUILD / "husk_entry.py"
    entry.write_text(ENTRY, "utf-8")
    args = [
        sys.executable, "-m", "PyInstaller", str(entry), "--name", NAME, "--onedir", "--noupx", "--noconfirm", "--clean",
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        "--paths", str(ROOT),
        "--add-data", f"{FRONTEND / 'dist'}{SEP}app/frontend/dist",
        "--add-data", f"{ROOT / 'app' / 'gateways' / 'sqlite' / 'migrations'}{SEP}app/gateways/sqlite/migrations",
        "--add-data", f"{ROOT / 'app' / 'gateways' / 'neon' / 'husk_core_schema.sql'}{SEP}app/gateways/neon",
        "--add-data", f"{ROOT / 'app' / 'i18n' / 'locales'}{SEP}app/i18n/locales",
    ]
    for pkg in COLLECT_ALL:
        args += ["--collect-all", pkg]
    for m in EXCLUDE:
        args += ["--exclude-module", m]
    for h in HIDDEN:
        args += ["--hidden-import", h]
    if os.name == "nt":
        args += ["--version-file", str(windows_version_file(version()))]
    subprocess.run(args, check=True, cwd=ROOT)
    return DIST / NAME


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke(folder: Path) -> None:
    exe = folder / (NAME + (".exe" if os.name == "nt" else ""))
    port = free_port()
    env = dict(os.environ, HUSK_PORT=str(port), HUSK_ENV="prod")
    # --data-dir: the same flag a server needs (a headless CI machine counts as one)
    proc = subprocess.Popen([str(exe), "--data-dir", str((BUILD / "smoke-data").resolve())], env=env)
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                    print("smoke test /health:", r.read().decode())
                    return
            except OSError:
                time.sleep(1)
        sys.exit("smoke test fallito: /health non risponde")
    finally:
        proc.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ui", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    if not a.skip_ui:
        build_ui()
    out = build_binary()
    print(f"built {out}")
    if a.smoke:
        smoke(out)
