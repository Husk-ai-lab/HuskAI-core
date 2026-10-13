"""Connector credentials: the private-file store (default) never touches the keychain and never leaks."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
import types
from pathlib import Path

import pytest

from app.gateways.connectors.secrets import ConnectorSecrets
from app.helpers.paths import DataPaths
from app.helpers.settings import Settings

ORG = "org_test"
BLOB = {"address": "ufficio@example.com", "password": "abcdefghijklmnop", "hint": "ufficio@example.com"}


@pytest.fixture
def no_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any keychain access fails the test: the file store must never reach for it (that is the prompt)."""

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("the keychain was touched")

    fake = types.SimpleNamespace(get_password=boom, set_password=boom, delete_password=boom)
    monkeypatch.setitem(sys.modules, "keyring", fake)


def test_round_trip_without_the_keychain(tmp_path: Path, no_keychain: None) -> None:
    store = ConnectorSecrets(file=tmp_path / "connector-secrets.json")
    assert store.get(ORG, "gmail") is None
    store.set(ORG, "gmail", BLOB)
    store.set(ORG, "telegram", {"token": "123:abc", "chat_id": "42"})
    assert store.get(ORG, "gmail") == BLOB
    # a new instance (a restart) reads the same file
    again = ConnectorSecrets(file=tmp_path / "connector-secrets.json")
    assert again.get(ORG, "gmail") == BLOB and again.get(ORG, "telegram")["chat_id"] == "42"
    again.delete(ORG, "gmail")
    assert again.get(ORG, "gmail") is None and again.get(ORG, "telegram") is not None
    again.delete(ORG, "gmail")  # deleting twice is fine


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_file_is_owner_only(tmp_path: Path) -> None:
    f = tmp_path / "connector-secrets.json"
    store = ConnectorSecrets(file=f)
    store.set(ORG, "gmail", BLOB)
    store.set(ORG, "gmail", {**BLOB, "password": "zzzzzzzzzzzzzzzz"})  # rewrite keeps the mode
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert not (tmp_path / "connector-secrets.json.tmp").exists()
    assert json.loads(f.read_text())  # valid JSON


def test_a_corrupt_file_reads_as_not_configured_and_can_be_replaced(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    f = tmp_path / "connector-secrets.json"
    f.write_text("{not json")
    store = ConnectorSecrets(file=f)
    assert store.get(ORG, "gmail") is None
    store.set(ORG, "gmail", BLOB)
    assert store.get(ORG, "gmail") == BLOB
    assert BLOB["password"] not in "".join(r.getMessage() for r in caplog.records)


def test_an_unwritable_location_keeps_the_secret_in_memory(tmp_path: Path) -> None:
    blocker = tmp_path / "file-not-a-folder"
    blocker.write_text("x")
    store = ConnectorSecrets(file=blocker / "connector-secrets.json")  # parent is a file: cannot be created
    store.set(ORG, "gmail", BLOB)
    assert store.get(ORG, "gmail") == BLOB  # this process still works; it will not survive a restart


def test_the_store_is_picked_from_the_setting(tmp_path: Path) -> None:
    paths = DataPaths(root=tmp_path)
    assert paths.connector_secrets == tmp_path / "connector-secrets.json"
    mk = lambda **kw: Settings(_env_file=None, data_dir=tmp_path, **kw)  # noqa: E731
    default = ConnectorSecrets.from_settings(mk(env="prod"), paths)
    assert default.file == paths.connector_secrets and not default.use_keyring
    keychain = ConnectorSecrets.from_settings(mk(env="prod", secret_store="keychain"), paths)
    assert keychain.file is None and keychain.use_keyring
    test_env = ConnectorSecrets.from_settings(mk(env="test"), paths)
    assert test_env.file is None and not test_env.use_keyring  # tests stay in memory


def test_the_secret_store_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HUSK_SECRET_STORE", "keychain")
    assert Settings(_env_file=None, data_dir=tmp_path).secret_store == "keychain"
    monkeypatch.delenv("HUSK_SECRET_STORE")
    assert Settings(_env_file=None, data_dir=tmp_path).secret_store == "file"
