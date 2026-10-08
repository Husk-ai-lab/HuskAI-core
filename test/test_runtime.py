"""Desktop vs server: on a server the OS data folder is never used and `python -m app.main` needs --data-dir."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import app.helpers.paths as paths_mod
from app import main
from app.helpers.paths import DataDirRequired, resolve_data_paths
from app.helpers.runtime import PLATFORM_ENV_VARS, detect_environment
from app.helpers.settings import Settings, get_settings

DISPLAY = {"DISPLAY": ":0"}


def detect(env: dict | None = None, platform: str = "linux", files: tuple[str, ...] = (), cgroup: str = ""):  # type: ignore[no-untyped-def]
    return detect_environment(env=env or {}, platform=platform, exists=lambda p: p in files, read_text=lambda p: cgroup)


# --- detection -----------------------------------------------------------------------------------
@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_a_mac_or_windows_computer_is_a_desktop(platform: str) -> None:
    info = detect(platform=platform)  # no display variable needed on these systems
    assert info.kind == "desktop" and info.reasons == () and not info.is_server


def test_a_linux_desktop_with_a_display_is_a_desktop() -> None:
    assert detect(DISPLAY).kind == "desktop"
    assert detect({"WAYLAND_DISPLAY": "wayland-0"}).kind == "desktop"


def test_linux_without_a_display_is_a_server() -> None:
    info = detect()
    assert info.is_server and info.reasons == ("headless-linux",)


@pytest.mark.parametrize("name", PLATFORM_ENV_VARS)
def test_hosting_platform_variables_mean_server(name: str) -> None:
    info = detect({**DISPLAY, name: "x"}, platform="darwin")
    assert info.is_server and info.reasons == (f"env:{name}",)


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_explicit_flag_means_server(value: str) -> None:
    assert detect({**DISPLAY, "HUSK_SERVER": value}, platform="darwin").reasons == ("HUSK_SERVER",)
    assert detect({**DISPLAY, "HUSK_SERVER": "0"}, platform="darwin").kind == "desktop"


def test_containers_are_servers_even_with_a_display() -> None:
    assert detect(DISPLAY, files=("/.dockerenv",)).reasons == ("file:/.dockerenv",)
    assert detect(DISPLAY, files=("/run/.containerenv",)).is_server
    assert detect(DISPLAY, cgroup="0::/kubepods/burstable/pod1").reasons == ("cgroup:kubepods",)
    assert detect(DISPLAY, platform="darwin", files=("/.dockerenv",)).kind == "desktop"  # Linux-only markers


def test_reasons_are_names_never_values() -> None:
    info = detect({"FLY_APP_NAME": "secret-app-name"})
    assert "secret-app-name" not in str(info)


# --- data folder fallback ---------------------------------------------------------------------------
def test_the_os_folder_is_never_the_fallback_on_a_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_mod, "user_data_dir", lambda *a, **k: str(tmp_path / "OS"))
    monkeypatch.setenv("HUSK_SERVER", "1")
    with pytest.raises(DataDirRequired):
        resolve_data_paths(Settings(HUSK_ENV="prod", HUSK_DATA_DIR=None))
    assert not (tmp_path / "OS").exists()
    # an explicit folder is fine on a server
    assert resolve_data_paths(Settings(HUSK_ENV="prod", HUSK_DATA_DIR=tmp_path / "d")).root == tmp_path / "d"


# --- main.run() -------------------------------------------------------------------------------------------
@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Replace uvicorn: records what run() would have served, using the settings it built."""
    import uvicorn

    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append({"app": app, **kw}))
    monkeypatch.delenv("HUSK_SERVER", raising=False)
    monkeypatch.setenv("HUSK_DATA_DIR", "")  # restored after the test, whatever run() sets
    monkeypatch.setenv("HUSK_ENV", "test")
    get_settings.cache_clear()
    yield calls
    get_settings.cache_clear()


def test_run_on_a_server_without_the_flag_exits_2_and_creates_nothing(started, monkeypatch, tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HUSK_SERVER", "1")
    with pytest.raises(SystemExit) as exc:
        main.run([])
    assert exc.value.code == 2 and started == []
    err = capsys.readouterr().err
    assert "--data-dir" in err and "HUSK_SERVER" in err


def test_the_environment_variable_alone_is_not_enough_on_a_server(started, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HUSK_SERVER", "1")
    monkeypatch.setenv("HUSK_DATA_DIR", str(tmp_path / "from-env"))
    with pytest.raises(SystemExit) as exc:
        main.run([])
    assert exc.value.code == 2 and started == [] and not (tmp_path / "from-env").exists()


def test_the_flag_starts_a_server_and_wins_over_the_environment(started, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HUSK_SERVER", "1")
    monkeypatch.setenv("HUSK_DATA_DIR", str(tmp_path / "from-env"))
    main.run(["--data-dir", str(tmp_path / "srv" / "husk")])
    assert len(started) == 1 and get_settings().data_dir == tmp_path / "srv" / "husk"
    assert (tmp_path / "srv" / "husk").is_dir() and not (tmp_path / "from-env").exists()


def test_a_relative_or_unwritable_path_exits_2(started, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as exc:
        main.run(["--data-dir", "relative/path"])
    assert exc.value.code == 2
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        with pytest.raises(SystemExit) as exc:
            main.run(["--data-dir", str(locked / "inner")])
        assert exc.value.code == 2 and started == []
    finally:
        locked.chmod(0o700)


def test_on_a_desktop_the_flag_is_optional(started, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(main, "detect_environment", lambda: detect(DISPLAY, platform="darwin"))
    main.run([])
    assert len(started) == 1
    main.run(["--data-dir", str(tmp_path / "x")])
    assert get_settings().data_dir == tmp_path / "x"
