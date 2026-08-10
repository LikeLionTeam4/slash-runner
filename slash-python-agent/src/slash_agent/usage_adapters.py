"""ClaudeCodeAdapter·CodexAdapter — 로컬에 설치된 Claude Code/Codex CLI의 SDK 사용량 조회 (w3-08).

두 CLI 모두 실행 파일을 직접 호출하는 공식 "usage" 서브커맨드가 없다 — 대신 로컬에 세션마다
JSONL 로그를 남기는데, 그 로그를 직접 읽어 집계하는 방식이 커뮤니티 도구(ccusage 등)의
표준 관례다. 이 파일도 같은 방식을 쓴다(CLI 실행 파일 자체가 PATH에 없어도 동작한다 —
로그 파일만 있으면 됨).

Claude Code(`~/.claude/projects/**/*.jsonl`)·Codex(`$CODEX_HOME 또는 ~/.codex/sessions/
**/rollout-*.jsonl`) 둘 다 이 프로젝트에서 실제 로그를 열어 직접 확인·검증했다(합성
픽스처 시험 + 이 머신에 실제로 남아있던 진짜 세션 데이터로 real mock-api 왕복까지).

각 CLI 어느 쪽으로 실행했든(터미널 대화형, `-p` 비대화형, VS Code 확장 등) 전부 같은
경로에 세션 로그를 남긴다 — 이 어댑터가 그 경로 하나만 보면 충분하다. 다만 로컬 파일
기반이라 원격에서 도는 세션(예: claude.ai/code 웹)은 이 머신에 파일이 안 남아 구조적으로
못 잡는다. Xcode Claude 연동은 별도 경로(`~/Library/Developer/Xcode/CodingAssistant/
ClaudeAgentConfig/projects/`)를 쓰는데, 이번 사용자층(터미널·VS Code 중심)과는 거리가
멀어 일부러 범위에서 뺐다 — 나중에 필요해지면 `_claude_code_root()`에 폴백 경로로 추가.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .protocol import now_iso_kst


def _claude_code_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _codex_root() -> Path:
    # Codex CLI 자신도 이 환경변수로 저장 위치를 바꾼다 — 안 쓰는 사람이 대다수라 기본값은
    # ~/.codex/sessions 그대로.
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home) if codex_home else Path.home() / ".codex"
    return base / "sessions"


def collect_claude_code_usage(root: Optional[Path] = None) -> Optional[dict]:
    """
    세션 파일(jsonl) 하나당 여러 assistant 턴이 있고, 각 턴의 message.usage는 그 턴 하나의
    값(Anthropic Messages API 관례 — 누적 아님)이라 파일 안의 모든 usage를 더하면 그 세션의
    합계가 나온다.
    """
    root = root or _claude_code_root()
    if not root.exists():
        return None

    total_sessions = 0
    total_input = total_output = total_cache_read = total_cache_creation = 0
    oldest: Optional[str] = None
    newest: Optional[str] = None

    for path in root.rglob("*.jsonl"):
        session_has_usage = False
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    usage = entry.get("message", {}).get("usage") if isinstance(entry.get("message"), dict) else None
                    if not usage:
                        continue
                    session_has_usage = True
                    total_input += usage.get("input_tokens", 0) or 0
                    total_output += usage.get("output_tokens", 0) or 0
                    total_cache_read += usage.get("cache_read_input_tokens", 0) or 0
                    total_cache_creation += usage.get("cache_creation_input_tokens", 0) or 0
                    timestamp = entry.get("timestamp")
                    if isinstance(timestamp, str):
                        if oldest is None or timestamp < oldest:
                            oldest = timestamp
                        if newest is None or timestamp > newest:
                            newest = timestamp
        except OSError:
            continue
        if session_has_usage:
            total_sessions += 1

    total_cached = total_cache_read + total_cache_creation
    return {
        "provider": "CLAUDE_CODE",
        "totalSessions": total_sessions,
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalCachedTokens": total_cached,
        "totalReasoningTokens": None,
        "totalTokens": total_input + total_output + total_cached,
        "oldestSessionAt": oldest,
        "newestSessionAt": newest,
        "collectedAt": now_iso_kst(),
    }


def collect_codex_usage(root: Optional[Path] = None) -> Optional[dict]:
    """
    token_count 이벤트의 total_token_usage는 그 시점까지의 누적 스냅샷이다(턴 하나의 값이
    아님) — Claude Code와 달리 더하면 안 되고, 세션의 마지막 이벤트 하나만 그 세션 합계로 쓴다.
    """
    root = root or _codex_root()
    if not root.exists():
        return None

    total_sessions = 0
    total_input = total_output = total_cached = total_reasoning = 0
    oldest: Optional[str] = None
    newest: Optional[str] = None

    for path in root.rglob("rollout-*.jsonl"):
        last_usage: Optional[dict] = None
        last_timestamp: Optional[str] = None
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = entry.get("payload")
                    if not isinstance(payload, dict) or payload.get("type") != "token_count":
                        continue
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        continue
                    usage = info.get("total_token_usage")
                    if not isinstance(usage, dict):
                        continue
                    last_usage = usage
                    timestamp = entry.get("timestamp")
                    if isinstance(timestamp, str):
                        last_timestamp = timestamp
        except OSError:
            continue

        if last_usage is None:
            continue
        total_sessions += 1
        total_input += last_usage.get("input_tokens", 0) or 0
        total_output += last_usage.get("output_tokens", 0) or 0
        total_cached += last_usage.get("cached_input_tokens", 0) or 0
        total_reasoning += last_usage.get("reasoning_output_tokens", 0) or 0
        if last_timestamp:
            if oldest is None or last_timestamp < oldest:
                oldest = last_timestamp
            if newest is None or last_timestamp > newest:
                newest = last_timestamp

    return {
        "provider": "CODEX",
        "totalSessions": total_sessions,
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalCachedTokens": total_cached,
        "totalReasoningTokens": total_reasoning,
        "totalTokens": total_input + total_output + total_cached + total_reasoning,
        "oldestSessionAt": oldest,
        "newestSessionAt": newest,
        "collectedAt": now_iso_kst(),
    }


COLLECTORS = {
    "CLAUDE_CODE": collect_claude_code_usage,
    "CODEX": collect_codex_usage,
}
