"""code_adapters.py 단위 시험 — subprocess/shutil.which를 몽키패치해 실제 claude/codex
CLI 없이도 가용성 판정·JSON 파싱 로직을 검증한다.
"""

import io
import subprocess
import threading

import pytest

import slash_agent.code_adapters as code_adapters
from slash_agent.code_adapters import (
    CodeAdapterError,
    CodeAdapterNotConfiguredError,
    ProjectWorkspaceConfig,
    run_claude_code_analysis,
    run_codex_analysis,
)


class TestAvailability:
    def test_claude_code_available_reflects_which(self, monkeypatch):
        monkeypatch.setattr(code_adapters.shutil, "which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)
        assert code_adapters.claude_code_available() is True
        assert code_adapters.codex_available() is False


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestClaudeCodeAnalysis:
    def test_raises_not_configured_when_cli_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: False)
        with pytest.raises(CodeAdapterNotConfiguredError):
            run_claude_code_analysis(tmp_path, "이 코드베이스 구조 설명해줘")

    def test_parses_result_string_schema(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: FakeCompletedProcess(returncode=0, stdout='{"result": "요약입니다", "num_turns": 3}'),
        )
        result = run_claude_code_analysis(tmp_path, "설명해줘")
        assert result["codeAdapter"] == "CLAUDE_CODE"
        assert result["summary"] == "요약입니다"
        assert result["turns"] == 3

    def test_parses_result_content_list_schema(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)
        payload = '{"result": {"content": [{"type": "text", "text": "부분1"}, {"type": "text", "text": "부분2"}]}, "num_turns": 2}'
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout=payload))
        result = run_claude_code_analysis(tmp_path, "설명해줘")
        assert result["summary"] == "부분1\n부분2"
        assert result["turns"] == 2

    def test_falls_back_to_raw_output_on_unknown_schema(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout="이건 JSON이 아님"))
        result = run_claude_code_analysis(tmp_path, "설명해줘")
        assert result["summary"] == "이건 JSON이 아님"
        assert result["turns"] is None

    def test_raises_code_adapter_error_on_nonzero_exit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="인증 안 됨"))
        with pytest.raises(CodeAdapterError):
            run_claude_code_analysis(tmp_path, "설명해줘")

    def test_raises_code_adapter_error_on_timeout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)

        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(CodeAdapterError):
            run_claude_code_analysis(tmp_path, "설명해줘", timeout_s=1)


class FakePopen:
    """Popen(stdout=PIPE, stderr=PIPE, text=True) 대역 — 라인 리스트를 스트리밍처럼 순회시킨다."""

    def __init__(self, stdout_lines, returncode=0, stderr_text=""):
        self.stdout = iter(stdout_lines)
        self.stderr = io.StringIO(stderr_text)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class FakeHangingStdout:
    """kill()이 호출되기 전까지는 다음 줄이 영영 안 오는 것처럼 블로킹한다(타임아웃 시험용)."""

    def __init__(self):
        self._killed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        self._killed.wait()
        raise StopIteration


class FakeHangingPopen:
    def __init__(self, *a, **k):
        self.stdout = FakeHangingStdout()
        self.stderr = io.StringIO("")
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.stdout._killed.set()


class TestCodexAnalysis:
    def test_raises_not_configured_when_cli_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: False)
        with pytest.raises(CodeAdapterNotConfiguredError):
            run_codex_analysis(tmp_path, "이 코드베이스 구조 설명해줘")

    def test_parses_last_agent_message_event(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        stdout_lines = [
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "첫 번째"}}\n',
            '{"type": "other_event"}\n',
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "마지막"}}\n',
        ]
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakePopen(stdout_lines))
        result = run_codex_analysis(tmp_path, "설명해줘")
        assert result["codeAdapter"] == "CODEX"
        assert result["summary"] == "마지막"
        assert result["turns"] == 2

    def test_falls_back_to_raw_output_when_no_agent_message(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakePopen(["예상 밖 출력\n"]))
        result = run_codex_analysis(tmp_path, "설명해줘")
        assert result["summary"] == "예상 밖 출력"
        assert result["turns"] is None

    def test_raises_code_adapter_error_on_nonzero_exit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        monkeypatch.setattr(
            subprocess, "Popen", lambda *a, **k: FakePopen([], returncode=1, stderr_text="샌드박스 거부")
        )
        with pytest.raises(CodeAdapterError):
            run_codex_analysis(tmp_path, "설명해줘")

    def test_calls_on_turn_complete_as_each_turn_finishes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        stdout_lines = [
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "첫 번째"}}\n',
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "두 번째"}}\n',
        ]
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakePopen(stdout_lines))
        seen_turns = []
        result = run_codex_analysis(tmp_path, "설명해줘", on_turn_complete=seen_turns.append)
        assert seen_turns == [1, 2]
        assert result["turns"] == 2

    def test_raises_code_adapter_error_on_timeout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeHangingPopen())
        with pytest.raises(CodeAdapterError):
            run_codex_analysis(tmp_path, "설명해줘", timeout_s=0.05)


class TestProjectWorkspaceConfig:
    def test_detects_git_repository(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(code_adapters, "AVAILABILITY_CHECKS", {"CLAUDE_CODE": lambda: True, "CODEX": lambda: False})
        workspace = ProjectWorkspaceConfig.from_root_path("w1", "내 프로젝트", str(tmp_path))
        assert workspace.workspace_type == "GIT_REPOSITORY"
        assert workspace.available_code_adapters == ["CLAUDE_CODE"]

    def test_detects_plain_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(code_adapters, "AVAILABILITY_CHECKS", {"CLAUDE_CODE": lambda: False, "CODEX": lambda: False})
        workspace = ProjectWorkspaceConfig.from_root_path("w1", "내 폴더", str(tmp_path))
        assert workspace.workspace_type == "DIRECTORY"
        assert workspace.available_code_adapters == []
