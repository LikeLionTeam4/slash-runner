"""code_adapters.py 단위 시험 — subprocess/shutil.which를 몽키패치해 실제 claude/codex
CLI 없이도 가용성 판정·JSON 파싱 로직을 검증한다.
"""

import subprocess

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


class TestCodexAnalysis:
    def test_raises_not_configured_when_cli_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: False)
        with pytest.raises(CodeAdapterNotConfiguredError):
            run_codex_analysis(tmp_path, "이 코드베이스 구조 설명해줘")

    def test_parses_last_agent_message_event(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        stdout = "\n".join(
            [
                '{"type": "item.completed", "item": {"type": "agent_message", "text": "첫 번째"}}',
                '{"type": "other_event"}',
                '{"type": "item.completed", "item": {"type": "agent_message", "text": "마지막"}}',
            ]
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout=stdout))
        result = run_codex_analysis(tmp_path, "설명해줘")
        assert result["codeAdapter"] == "CODEX"
        assert result["summary"] == "마지막"
        assert result["turns"] == 2

    def test_falls_back_to_raw_output_when_no_agent_message(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout="예상 밖 출력"))
        result = run_codex_analysis(tmp_path, "설명해줘")
        assert result["summary"] == "예상 밖 출력"
        assert result["turns"] is None

    def test_raises_code_adapter_error_on_nonzero_exit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="샌드박스 거부"))
        with pytest.raises(CodeAdapterError):
            run_codex_analysis(tmp_path, "설명해줘")


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
