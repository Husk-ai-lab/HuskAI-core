"""Where is Husk running: on someone's computer ("desktop") or on a server?

On a desktop the data lives in the OS application-data folder (`helpers/paths.py`). On a server that folder must
never be used by accident: the data folder has to be named on purpose with `--data-dir` (see `app/main.py`).

Any ONE signal means "server": an explicit `HUSK_SERVER=1`, a container (Docker, Podman, Kubernetes), a hosting
platform's environment variables, or Linux without a display. Reasons are signal names only, never values.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# set by the platforms Husk is likely to be deployed on (presence is enough)
PLATFORM_ENV_VARS = (
    "KUBERNETES_SERVICE_HOST", "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "FLY_APP_NAME", "RENDER", "DYNO", "K_SERVICE",
    "ECS_CONTAINER_METADATA_URI", "ECS_CONTAINER_METADATA_URI_V4", "WEBSITE_SITE_NAME", "AWS_EXECUTION_ENV",
)
CONTAINER_FILES = ("/.dockerenv", "/run/.containerenv")
CGROUP_MARKERS = ("docker", "kubepods", "containerd", "libpod")
_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeInfo:
    kind: Literal["desktop", "server"]
    reasons: tuple[str, ...] = ()

    @property
    def is_server(self) -> bool:
        return self.kind == "server"


def _read(path: str) -> str:
    try:
        return Path(path).read_text(errors="ignore")
    except OSError:
        return ""


def detect_environment(
    env: Mapping[str, str] | None = None, platform: str | None = None,
    exists: Callable[[str], bool] = os.path.exists, read_text: Callable[[str], str] = _read,
) -> RuntimeInfo:
    """The arguments exist for tests; the defaults look at this process and this machine."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    reasons: list[str] = []
    if env.get("HUSK_SERVER", "").strip().lower() in _TRUE:
        reasons.append("HUSK_SERVER")
    reasons += [f"env:{name}" for name in PLATFORM_ENV_VARS if env.get(name)]
    if platform.startswith("linux"):
        reasons += [f"file:{f}" for f in CONTAINER_FILES if exists(f)]
        cgroup = read_text("/proc/1/cgroup")
        reasons += [f"cgroup:{m}" for m in CGROUP_MARKERS if m in cgroup]
        if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):  # macOS and Windows are never judged headless
            reasons.append("headless-linux")
    return RuntimeInfo("server" if reasons else "desktop", tuple(reasons))
