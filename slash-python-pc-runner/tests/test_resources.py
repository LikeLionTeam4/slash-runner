"""resources.py의 config_dir()/migrate_legacy_config_dir() 시험.

macOS 폴더명을 slash-pc-runner-py에서 slash로 바꾸면서, 기존 사용자의 설정·페어링
정보가 유실되지 않는지가 핵심이다.
"""

from __future__ import annotations

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
