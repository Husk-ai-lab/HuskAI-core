"""Show a file in the operating system's file manager (Finder / Explorer / the folder on Linux).

Used for "Show in folder" after a backup is written: the app window is a webview that cannot save downloads, so the
backup is written into the data folder and the person is pointed at it. The path is always built by the caller from
a validated name inside the data folder; it is passed as an argument, never through a shell."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def reveal_command(path: Path, platform: str | None = None) -> list[str]:
    platform = platform or sys.platform
    if platform == "darwin":
        return ["open", "-R", str(path)]
    if platform.startswith("win"):
        return ["explorer", f"/select,{path}"]
    return ["xdg-open", str(path.parent)]


def reveal(path: Path) -> None:
    """Raises OSError when the file manager cannot be started."""
    subprocess.Popen(reveal_command(path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                     stderr=subprocess.DEVNULL, close_fds=True)
