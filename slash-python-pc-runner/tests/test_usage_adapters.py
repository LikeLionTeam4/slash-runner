"""usage_adapters.py 단위 시험 — w3-08(ClaudeCodeAdapter·CodexAdapter) 대응.

Claude Code 쪽은 합성 픽스처 검증 외에, 이 머신의 실제 ~/.claude/projects/를 대상으로도
한 번 더 돌려본다(내용 검증이 아니라 진짜 데이터로 에러 없이 합계가 나오는지만).
"""

import json
from pathlib import Path

import slash_pc_runner.usage_adapters as usage_adapters
from slash_pc_runner.usage_adapters import (
    _claude_code_root,
    _codex_root,
    collect_claude_code_usage,
    collect_codex_usage,
)


def write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


class TestClaudeCodeAdapter:
    def test_missing_root_returns_none(self, tmp_path):
        assert collect_claude_code_usage(tmp_path / "does-not-exist") is None

    def test_no_sessions_returns_zeroed_result(self, tmp_path):
        root = tmp_path / "projects"
        root.mkdir()
        result = collect_claude_code_usage(root)
        assert result["totalSessions"] == 0
        assert result["totalTokens"] == 0

    def test_sums_usage_across_turns_and_sessions(self, tmp_path):
        root = tmp_path / "projects"
        write_jsonl(
            root / "proj-a" / "session-1.jsonl",
            [
                {"type": "session_start", "timestamp": "2026-08-01T10:00:00.000Z", "sessionId": "s1"},
                {
                    "type": "assistant",
                    "timestamp": "2026-08-01T10:00:00.000Z",
                    "message": {"usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 3}},
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-08-01T10:05:00.000Z",
                    "message": {"usage": {"input_tokens": 15, "output_tokens": 25, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
                },
            ],
        )
        write_jsonl(
            root / "proj-b" / "session-2.jsonl",
            [
                {
                    "type": "assistant",
                    "timestamp": "2026-08-02T09:00:00.000Z",
                    "message": {"usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
                },
            ],
        )

        result = collect_claude_code_usage(root)
        assert result["provider"] == "CLAUDE_CODE"
        assert result["totalSessions"] == 2
        assert result["totalInputTokens"] == 125
        assert result["totalOutputTokens"] == 95
        assert result["totalCachedTokens"] == 8
        assert result["totalReasoningTokens"] is None
        assert result["totalTokens"] == 125 + 95 + 8
        assert result["oldestSessionAt"] == "2026-08-01T10:00:00.000Z"
        assert result["newestSessionAt"] == "2026-08-02T09:00:00.000Z"

    def test_skips_corrupted_lines(self, tmp_path):
        root = tmp_path / "projects"
        path = root / "proj-a" / "session-1.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(
            '{"broken json\n'
            + json.dumps({"type": "assistant", "timestamp": "2026-08-01T10:00:00.000Z", "message": {"usage": {"input_tokens": 10, "output_tokens": 20}}})
            + "\n",
            encoding="utf-8",
        )
        result = collect_claude_code_usage(root)
        assert result["totalSessions"] == 1
        assert result["totalInputTokens"] == 10

    def test_real_local_data_does_not_error(self):
        """진짜 ~/.claude/projects/를 대상으로 — 이 머신에 실제로 존재하는 데이터."""
        result = collect_claude_code_usage(_claude_code_root())
        assert result is not None
        assert result["totalSessions"] >= 0
        assert result["totalTokens"] >= 0


class TestCodexAdapter:
    def test_missing_root_returns_none(self, tmp_path):
        assert collect_codex_usage(tmp_path / "does-not-exist") is None

    def test_uses_last_cumulative_snapshot_not_sum(self, tmp_path):
        root = tmp_path / "sessions"
        write_jsonl(
            root / "2026" / "08" / "01" / "rollout-abc.jsonl",
            [
                {
                    "timestamp": "2026-08-01T11:00:00.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 20, "reasoning_output_tokens": 5, "total_tokens": 85}},
                    },
                },
                {
                    "timestamp": "2026-08-01T11:05:00.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 80, "cached_input_tokens": 15, "output_tokens": 40, "reasoning_output_tokens": 10, "total_tokens": 145}},
                    },
                },
            ],
        )

        result = collect_codex_usage(root)
        assert result["provider"] == "CODEX"
        assert result["totalSessions"] == 1
        # 두 이벤트를 더하면 안 된다 — 마지막(누적) 값만 써야 함
        assert result["totalInputTokens"] == 80
        assert result["totalOutputTokens"] == 40
        assert result["totalCachedTokens"] == 15
        assert result["totalReasoningTokens"] == 10

    def test_multiple_sessions_sum_their_final_snapshots(self, tmp_path):
        root = tmp_path / "sessions"
        write_jsonl(
            root / "2026" / "08" / "01" / "rollout-a.jsonl",
            [{"timestamp": "t1", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 0, "reasoning_output_tokens": 0}}}}],
        )
        write_jsonl(
            root / "2026" / "08" / "02" / "rollout-b.jsonl",
            [{"timestamp": "t2", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 20, "output_tokens": 15, "cached_input_tokens": 0, "reasoning_output_tokens": 0}}}}],
        )

        result = collect_codex_usage(root)
        assert result["totalSessions"] == 2
        assert result["totalInputTokens"] == 30
        assert result["totalOutputTokens"] == 20

    def test_default_root_honors_codex_home_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom-codex-home"))
        assert _codex_root() == tmp_path / "custom-codex-home" / "sessions"

    def test_default_root_falls_back_to_home_dir(self, monkeypatch):
        monkeypatch.delenv("CODEX_HOME", raising=False)
        assert _codex_root() == Path.home() / ".codex" / "sessions"


class TestUsageCache:
    def test_repeated_call_within_ttl_does_not_reread_disk(self, tmp_path):
        usage_adapters._cache.clear()
        root = tmp_path / "projects"
        write_jsonl(
            root / "proj-a" / "session-1.jsonl",
            [{"type": "assistant", "timestamp": "t1", "message": {"usage": {"input_tokens": 10, "output_tokens": 5}}}],
        )

        first = collect_claude_code_usage(root)
        assert first["totalInputTokens"] == 10

        # 캐시 TTL 안에서 파일이 바뀌어도(세션 추가) 재조회 결과는 캐시된 값 그대로다.
        write_jsonl(
            root / "proj-b" / "session-2.jsonl",
            [{"type": "assistant", "timestamp": "t2", "message": {"usage": {"input_tokens": 999, "output_tokens": 999}}}],
        )
        second = collect_claude_code_usage(root)
        assert second["totalInputTokens"] == 10
        assert second is first

    def test_call_after_ttl_expires_rereads_disk(self, tmp_path, monkeypatch):
        usage_adapters._cache.clear()
        root = tmp_path / "projects"
        write_jsonl(
            root / "proj-a" / "session-1.jsonl",
            [{"type": "assistant", "timestamp": "t1", "message": {"usage": {"input_tokens": 10, "output_tokens": 5}}}],
        )

        fake_clock = [1000.0]
        monkeypatch.setattr(usage_adapters.time, "monotonic", lambda: fake_clock[0])

        first = collect_claude_code_usage(root)
        assert first["totalInputTokens"] == 10

        write_jsonl(
            root / "proj-b" / "session-2.jsonl",
            [{"type": "assistant", "timestamp": "t2", "message": {"usage": {"input_tokens": 999, "output_tokens": 999}}}],
        )
        fake_clock[0] += usage_adapters._CACHE_TTL_S + 1
        second = collect_claude_code_usage(root)
        assert second["totalInputTokens"] == 1009

    def test_different_roots_are_cached_independently(self, tmp_path):
        usage_adapters._cache.clear()
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        write_jsonl(
            root_a / "proj" / "session.jsonl",
            [{"type": "assistant", "timestamp": "t1", "message": {"usage": {"input_tokens": 1, "output_tokens": 1}}}],
        )
        write_jsonl(
            root_b / "proj" / "session.jsonl",
            [{"type": "assistant", "timestamp": "t1", "message": {"usage": {"input_tokens": 2, "output_tokens": 2}}}],
        )

        assert collect_claude_code_usage(root_a)["totalInputTokens"] == 1
        assert collect_claude_code_usage(root_b)["totalInputTokens"] == 2
