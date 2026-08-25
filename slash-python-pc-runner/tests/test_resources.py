"""resources.py의 config_dir()/migrate_legacy_config_dir() 시험.

macOS 폴더명을 slash-pc-runner-py에서 slash로 바꾸면서, 기존 사용자의 설정·페어링
정보가 유실되지 않는지가 핵심이다.
"""

from __future__ import annotations

import os
from pathlib import Path

import slash_pc_runner.resources as resources


class TestConfigDir:
    def test_macos_uses_product_name_slash(self, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/testuser")))

        assert resources.config_dir() == Path("/Users/testuser/Library/Application Support/slash")

    def test_windows_keeps_package_name(self, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(Path("C:/Users/testuser/AppData/Roaming")))

        assert resources.config_dir() == Path("C:/Users/testuser/AppData/Roaming") / "slash-pc-runner-py"


class FakeCompletedProcess:
    def __init__(self, stdout=""):
        self.stdout = stdout


class TestResolveCliPath:
    def test_noop_on_windows(self, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "win32")
        called = []
        monkeypatch.setattr(resources.subprocess, "run", lambda *a, **k: called.append(1))

        resources.resolve_cli_path()

        assert called == []

    def test_appends_shell_path_entries_not_already_present(self, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "darwin")
        monkeypatch.setattr(os, "pathsep", ":")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setattr(
            resources.subprocess,
            "run",
            lambda *a, **k: FakeCompletedProcess(stdout="/usr/bin:/opt/homebrew/bin"),
        )

        resources.resolve_cli_path()

        assert os.environ["PATH"] == "/usr/bin:/bin:/opt/homebrew/bin"

    def test_leaves_path_untouched_when_shell_lookup_fails(self, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "darwin")
        monkeypatch.setattr(os, "pathsep", ":")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        def _raise(*a, **k):
            raise TimeoutError("no shell")

        monkeypatch.setattr(resources.subprocess, "run", _raise)

        resources.resolve_cli_path()

        assert os.environ["PATH"] == "/usr/bin:/bin"

    def test_leaves_path_untouched_when_no_new_entries(self, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "darwin")
        monkeypatch.setattr(os, "pathsep", ":")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setattr(
            resources.subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout="/usr/bin:/bin")
        )

        resources.resolve_cli_path()

        assert os.environ["PATH"] == "/usr/bin:/bin"


class TestUnblockOwnFiles:
    def test_noop_when_not_frozen(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resources.sys, "platform", "win32")
        monkeypatch.setattr(resources, "is_frozen", lambda: False)
        removed = []
        monkeypatch.setattr(resources.os, "remove", lambda p: removed.append(p))

        resources.unblock_own_files()

        assert removed == []

    def test_noop_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "darwin")
        monkeypatch.setattr(resources, "is_frozen", lambda: True)
        removed = []
        monkeypatch.setattr(resources.os, "remove", lambda p: removed.append(p))

        resources.unblock_own_files()

        assert removed == []

    def test_attempts_to_remove_zone_identifier_stream_for_every_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resources.sys, "platform", "win32")
        monkeypatch.setattr(resources, "is_frozen", lambda: True)
        exe_dir = tmp_path / "Slash-0.5.6"
        exe_dir.mkdir()
        monkeypatch.setattr(resources.sys, "executable", str(exe_dir / "Slash.exe"))
        (exe_dir / "Slash.exe").write_text("", encoding="utf-8")
        internal = exe_dir / "_internal"
        internal.mkdir()
        (internal / "Python.Runtime.dll").write_text("", encoding="utf-8")

        removed = []
        monkeypatch.setattr(resources.os, "remove", lambda p: removed.append(p))

        resources.unblock_own_files()

        assert f"{exe_dir / 'Slash.exe'}:Zone.Identifier" in removed
        assert f"{internal / 'Python.Runtime.dll'}:Zone.Identifier" in removed

    def test_swallows_errors_when_stream_already_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resources.sys, "platform", "win32")
        monkeypatch.setattr(resources, "is_frozen", lambda: True)
        exe_dir = tmp_path / "Slash-0.5.6"
        exe_dir.mkdir()
        monkeypatch.setattr(resources.sys, "executable", str(exe_dir / "Slash.exe"))
        (exe_dir / "Slash.exe").write_text("", encoding="utf-8")

        def _raise(p):
            raise OSError("stream not found")

        monkeypatch.setattr(resources.os, "remove", _raise)

        resources.unblock_own_files()  # 예외가 밖으로 나오지 않아야 한다


class TestMigrateLegacyConfigDir:
    def test_moves_old_directory_to_new_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        old_dir = tmp_path / "Library" / "Application Support" / "slash-pc-runner-py"
        old_dir.mkdir(parents=True)
        (old_dir / "config.json").write_text("{}", encoding="utf-8")

        resources.migrate_legacy_config_dir()

        new_dir = tmp_path / "Library" / "Application Support" / "slash"
        assert not old_dir.exists()
        assert (new_dir / "config.json").read_text(encoding="utf-8") == "{}"

    def test_does_nothing_when_new_dir_already_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        old_dir = tmp_path / "Library" / "Application Support" / "slash-pc-runner-py"
        old_dir.mkdir(parents=True)
        (old_dir / "config.json").write_text("old", encoding="utf-8")

        new_dir = tmp_path / "Library" / "Application Support" / "slash"
        new_dir.mkdir(parents=True)
        (new_dir / "config.json").write_text("new", encoding="utf-8")

        resources.migrate_legacy_config_dir()

        assert old_dir.exists()
        assert (new_dir / "config.json").read_text(encoding="utf-8") == "new"

    def test_does_nothing_when_old_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        resources.migrate_legacy_config_dir()

        new_dir = tmp_path / "Library" / "Application Support" / "slash"
        assert not new_dir.exists()

    def test_noop_on_windows(self, monkeypatch):
        monkeypatch.setattr(resources.sys, "platform", "win32")

        resources.migrate_legacy_config_dir()
