"""code_adapters.py 단위 시험 — subprocess/shutil.which를 몽키패치해 실제 claude/codex
CLI 없이도 가용성 판정·JSON 파싱 로직을 검증한다.
"""

import io
import subprocess
import threading

import pytest

import slash_pc_runner.code_adapters as code_adapters
from slash_pc_runner.code_adapters import (
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
    @pytest.fixture(autouse=True)
    def _no_weekly_usage_by_default(self, monkeypatch):
        # 실제 이 머신의 ~/.claude/projects/를 긁지 않게 기본값을 None으로 고정한다 —
        # 최근 7일 사용량 자체를 검증하는 시험만 이걸 다시 오버라이드한다.
        monkeypatch.setattr(code_adapters, "_last_7_days_usage", lambda provider: None)

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

    def test_raises_code_adapter_error_not_not_configured_when_workspace_missing(self, monkeypatch, tmp_path):
        # 실측 확인한 버그 재발 방지 — 워크스페이스 폴더가 없으면 subprocess.run이 CLI
        # 실행 파일이 없을 때와 똑같은 FileNotFoundError를 던진다. CLI는 있는데 폴더만
        # 없는 경우를 "CLI 설치 안 됨"(CodeAdapterNotConfiguredError)으로 잘못 보고하면
        # 안 된다 — CodeAdapterError여야 하고, 메시지에 원인이 정확히 남아야 한다.
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)
        missing_path = tmp_path / "존재안함"

        with pytest.raises(CodeAdapterError, match="워크스페이스 폴더를 찾을 수 없습니다") as exc_info:
            run_claude_code_analysis(missing_path, "설명해줘")

        assert not isinstance(exc_info.value, CodeAdapterNotConfiguredError)

    def test_includes_this_run_usage_from_cli_response(self, monkeypatch, tmp_path):
        # claude -p --output-format json은 usage·total_cost_usd를 최상위에 직접 준다
        # (실측 확인). /code가 사용자의 다른 Claude Code 사용과 같은 구독 레이트리밋
        # 풀을 나눠 쓰기 때문에, 세션 로그를 다시 훑지 않고 이 응답에서 바로 뽑는다.
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)
        payload = (
            '{"result": "답", "num_turns": 1, "total_cost_usd": 0.05, '
            '"usage": {"input_tokens": 10, "output_tokens": 20, '
            '"cache_read_input_tokens": 3, "cache_creation_input_tokens": 7}}'
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout=payload))

        result = run_claude_code_analysis(tmp_path, "설명해줘")

        assert result["usage"]["thisRun"] == {
            "inputTokens": 10,
            "outputTokens": 20,
            "cachedTokens": 10,
            "costUsd": 0.05,
        }

    def test_includes_last_7_days_usage_for_matching_provider(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)
        seen_provider = []
        monkeypatch.setattr(
            code_adapters,
            "_last_7_days_usage",
            lambda provider: seen_provider.append(provider) or {"provider": "CLAUDE_CODE", "totalTokens": 999},
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout='{"result": "답"}'))

        result = run_claude_code_analysis(tmp_path, "설명해줘")

        assert seen_provider == ["CLAUDE_CODE"]
        assert result["usage"]["last7Days"] == {"provider": "CLAUDE_CODE", "totalTokens": 999}

    def test_this_run_usage_is_none_when_response_lacks_usage_field(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "claude_code_available", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout="일반 텍스트, JSON 아님"))

        result = run_claude_code_analysis(tmp_path, "설명해줘")

        assert result["usage"]["thisRun"] is None


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
    @pytest.fixture(autouse=True)
    def _no_weekly_usage_by_default(self, monkeypatch):
        # TestClaudeCodeAnalysis의 같은 이름 fixture와 동일한 이유 — 실제 이 머신의
        # ~/.codex/sessions/를 긁지 않게 기본값을 고정한다.
        monkeypatch.setattr(code_adapters, "_last_7_days_usage", lambda provider: None)

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

    def test_passes_skip_git_repo_check_flag(self, monkeypatch, tmp_path):
        """git 저장소가 아닌 DIRECTORY 워크스페이스에서는 이 플래그가 없으면 codex CLI가
        "Not inside a trusted directory" 로 거부한다(실제 CLI로 재현 확인함)."""
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        captured_args = {}

        def fake_popen(args, **kwargs):
            captured_args["args"] = args
            return FakePopen(['{"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}\n'])

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        run_codex_analysis(tmp_path, "설명해줘")
        assert "--skip-git-repo-check" in captured_args["args"]

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

    def test_raises_code_adapter_error_not_not_configured_when_workspace_missing(self, monkeypatch, tmp_path):
        # TestClaudeCodeAnalysis의 같은 이름 시험과 동일한 이유 — Codex도 같은 종류의
        # FileNotFoundError 오분류가 있었다.
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        missing_path = tmp_path / "존재안함"

        with pytest.raises(CodeAdapterError, match="워크스페이스 폴더를 찾을 수 없습니다") as exc_info:
            run_codex_analysis(missing_path, "설명해줘")

        assert not isinstance(exc_info.value, CodeAdapterNotConfiguredError)

    def test_includes_this_run_usage_from_turn_completed_event(self, monkeypatch, tmp_path):
        # codex exec ... --json은 turn.completed 이벤트에 usage를 직접 준다(실측 확인).
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        stdout_lines = [
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "답"}}\n',
            '{"type": "turn.completed", "usage": {"input_tokens": 15541, "cached_input_tokens": 11008, '
            '"cache_write_input_tokens": 0, "output_tokens": 7, "reasoning_output_tokens": 0}}\n',
        ]
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakePopen(stdout_lines))

        result = run_codex_analysis(tmp_path, "설명해줘")

        assert result["usage"]["thisRun"] == {
            "inputTokens": 15541,
            "outputTokens": 7,
            "cachedTokens": 11008,
            "costUsd": None,
        }

    def test_includes_last_7_days_usage_for_matching_provider(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        seen_provider = []
        monkeypatch.setattr(
            code_adapters,
            "_last_7_days_usage",
            lambda provider: seen_provider.append(provider) or {"provider": "CODEX", "totalTokens": 42},
        )
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **k: FakePopen(['{"type": "item.completed", "item": {"type": "agent_message", "text": "답"}}\n']),
        )

        result = run_codex_analysis(tmp_path, "설명해줘")

        assert seen_provider == ["CODEX"]
        assert result["usage"]["last7Days"] == {"provider": "CODEX", "totalTokens": 42}

    def test_this_run_usage_is_none_when_no_turn_completed_event(self, monkeypatch, tmp_path):
        monkeypatch.setattr(code_adapters, "codex_available", lambda: True)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakePopen(["예상 밖 출력\n"]))

        result = run_codex_analysis(tmp_path, "설명해줘")

        assert result["usage"]["thisRun"] is None

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
