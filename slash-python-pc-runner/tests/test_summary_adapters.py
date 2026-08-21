"""summary_adapters.py 단위 시험 — subprocess를 몽키패치해 실제 claude/codex CLI 없이도
가용성 판정·프롬프트 전달 방식(stdin)·도구 차단·JSON 파싱 로직을 검증한다.
"""

import subprocess

import pytest

import slash_pc_runner.summary_adapters as summary_adapters
from slash_pc_runner.summary_adapters import (
    SUMMARY_MAX_INPUT_CHARS,
    SummaryAdapterError,
    SummaryAdapterNotConfiguredError,
    run_claude_code_summary,
    run_codex_summary,
)


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestClaudeCodeSummary:
    def test_raises_not_configured_when_cli_missing(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: False)
        with pytest.raises(SummaryAdapterNotConfiguredError):
            run_claude_code_summary("요약할 글" * 30)

    def test_sends_text_via_stdin_not_as_argument(self, monkeypatch):
        # RUN-02 요구사항 — 사용자 입력을 CLI 인자가 아니라 표준입력으로 넘긴다.
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: True)
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            return FakeCompletedProcess(returncode=0, stdout='{"result": "요약"}')

        monkeypatch.setattr(subprocess, "run", fake_run)
        text = "민감할 수도 있는 원문 텍스트입니다"
        run_claude_code_summary(text)

        assert text in captured["input"]
        assert not any(text in str(arg) for arg in captured["args"])

    def test_disables_write_edit_bash_and_other_tools(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: True)
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return FakeCompletedProcess(returncode=0, stdout='{"result": "요약"}')

        monkeypatch.setattr(subprocess, "run", fake_run)
        run_claude_code_summary("요약할 글" * 30)

        idx = captured["args"].index("--disallowed-tools")
        disallowed = captured["args"][idx + 1]
        for tool in ("Write", "Edit", "Bash", "WebSearch", "WebFetch"):
            assert tool in disallowed

    def test_runs_in_empty_temporary_directory(self, monkeypatch):
        # RUN-02 요구사항 — 등록된 프로젝트 폴더가 아니라 매 실행마다 비어 있는 임시
        # 디렉터리에서 실행한다. cwd로 전달된 경로가 실제로 빈 디렉터리인지 확인한다.
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: True)
        captured = {}

        def fake_run(args, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            from pathlib import Path

            captured["cwd_contents"] = list(Path(kwargs["cwd"]).iterdir())
            return FakeCompletedProcess(returncode=0, stdout='{"result": "요약"}')

        monkeypatch.setattr(subprocess, "run", fake_run)
        run_claude_code_summary("요약할 글" * 30)

        assert captured["cwd"] is not None
        assert captured["cwd_contents"] == []

    def test_parses_result_string_schema(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: True)
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout='{"result": "짧은 요약"}')
        )
        result = run_claude_code_summary("요약할 글" * 30)
        assert result["summaryAdapter"] == "CLAUDE_CODE"
        assert result["summary"] == "짧은 요약"
        assert "durationMs" in result and "collectedAt" in result

    def test_falls_back_to_raw_output_on_unknown_schema(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout="JSON 아님"))
        result = run_claude_code_summary("요약할 글" * 30)
        assert result["summary"] == "JSON 아님"

    def test_raises_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="오류"))
        with pytest.raises(SummaryAdapterError):
            run_claude_code_summary("요약할 글" * 30)

    def test_raises_on_timeout(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: True)

        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SummaryAdapterError, match="시간 초과"):
            run_claude_code_summary("요약할 글" * 30, timeout_s=1)

    def test_rejects_input_over_max_chars_without_calling_cli(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "claude_code_available", lambda: True)

        def fail_if_called(*a, **k):
            raise AssertionError("CLI가 호출되면 안 된다 — 길이 검증이 먼저 실패해야 함")

        monkeypatch.setattr(subprocess, "run", fail_if_called)
        with pytest.raises(SummaryAdapterError, match="너무 깁니다"):
            run_claude_code_summary("가" * (SUMMARY_MAX_INPUT_CHARS + 1))


class TestCodexSummary:
    def test_raises_not_configured_when_cli_missing(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "codex_available", lambda: False)
        with pytest.raises(SummaryAdapterNotConfiguredError):
            run_codex_summary("요약할 글" * 30)

    def test_sends_text_via_stdin_and_uses_sandbox_flags(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "codex_available", lambda: True)
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            payload = '{"type": "item.completed", "item": {"type": "agent_message", "text": "요약"}}'
            return FakeCompletedProcess(returncode=0, stdout=payload)

        monkeypatch.setattr(subprocess, "run", fake_run)
        text = "codex로 요약할 원문"
        run_codex_summary(text)

        assert text in captured["input"]
        assert not any(text in str(arg) for arg in captured["args"])
        assert "--sandbox" in captured["args"] and "read-only" in captured["args"]
        assert "--skip-git-repo-check" in captured["args"]

    def test_parses_last_agent_message_from_ndjson(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "codex_available", lambda: True)
        payload = "\n".join(
            [
                '{"type": "thread.started"}',
                '{"type": "item.completed", "item": {"type": "agent_message", "text": "첫 응답"}}',
                '{"type": "item.completed", "item": {"type": "agent_message", "text": "최종 요약"}}',
                '{"type": "turn.completed"}',
            ]
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout=payload))
        result = run_codex_summary("요약할 글" * 30)
        assert result["summaryAdapter"] == "CODEX"
        assert result["summary"] == "최종 요약"

    def test_raises_when_no_agent_message_found(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "codex_available", lambda: True)
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=0, stdout='{"type": "thread.started"}')
        )
        with pytest.raises(SummaryAdapterError):
            run_codex_summary("요약할 글" * 30)

    def test_raises_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "codex_available", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="오류"))
        with pytest.raises(SummaryAdapterError):
            run_codex_summary("요약할 글" * 30)

    def test_raises_on_timeout(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "codex_available", lambda: True)

        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="codex", timeout=1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SummaryAdapterError, match="시간 초과"):
            run_codex_summary("요약할 글" * 30, timeout_s=1)

    def test_rejects_input_over_max_chars_without_calling_cli(self, monkeypatch):
        monkeypatch.setattr(summary_adapters, "codex_available", lambda: True)

        def fail_if_called(*a, **k):
            raise AssertionError("CLI가 호출되면 안 된다 — 길이 검증이 먼저 실패해야 함")

        monkeypatch.setattr(subprocess, "run", fail_if_called)
        with pytest.raises(SummaryAdapterError, match="너무 깁니다"):
            run_codex_summary("가" * (SUMMARY_MAX_INPUT_CHARS + 1))
