"""reveal_in_file_manager 단위 시험 — 플랫폼별로 올바른 명령을 구성하는지만 확인한다.
실제 Finder/탐색기는 절대 띄우지 않는다(subprocess.run을 항상 대체)."""

from pathlib import Path

import pytest

from slash_pc_runner import file_actions


def test_macos_uses_open_dash_r(monkeypatch):
    calls = []
    monkeypatch.setattr(file_actions.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(file_actions.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))

    file_actions.reveal_in_file_manager(Path("/tmp/문서.txt"))

    (args, kwargs) = calls[0]
    assert args[0] == ["open", "-R", "/tmp/문서.txt"]
    assert kwargs["check"] is True


def test_windows_uses_explorer_select_without_check(monkeypatch):
    calls = []
    monkeypatch.setattr(file_actions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(file_actions.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))

    file_actions.reveal_in_file_manager(Path(r"C:\Users\test\문서.txt"))

    (args, kwargs) = calls[0]
    assert args[0] == ["explorer", r"/select,C:\Users\test\문서.txt"]
    assert "check" not in kwargs  # explorer는 성공해도 종료 코드가 0이 아닐 수 있다


def test_unsupported_platform_raises(monkeypatch):
    monkeypatch.setattr(file_actions.platform, "system", lambda: "Linux")

    with pytest.raises(RuntimeError):
        file_actions.reveal_in_file_manager(Path("/tmp/a.txt"))
