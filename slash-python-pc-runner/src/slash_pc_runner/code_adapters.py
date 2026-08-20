"""ClaudeCodeAdapter·CodexAdapter 실행부 — CODE_ANALYSIS TaskType.

usage_adapters.py(로그만 읽음)와 달리 이건 로컬에 설치된 `claude`/`codex` CLI를 실제로
**실행**한다. 읽기 전용으로만 쓴다(파일 쓰기·셸 실행 도구는 CLI 플래그로 구조적으로 차단
— 프롬프트로만 막는 게 아니라 애초에 그 도구 자체를 못 쓰게 한다).

인증은 별도로 다루지 않는다 — Claude Code는 대화형 로그인과 헤드리스(`-p`) 모드가 인증
상태를 공유하고, Codex(`codex exec`)도 마찬가지로 로컬 로그인 상태를 그대로 쓴다. 즉
사용자가 터미널에서 이미 `claude login`/`codex login`을 해뒀다면 여기서 다시 로그인할
필요가 없다 — 그 반대로, 로그인이 안 돼 있으면 CLI가 알아서 실패하고 그 실패는
`CodeAdapterError`로 잡혀 RESULT.FAILED가 된다(원인이 "인증 안 됨"인지 "다른 오류"인지는
CLI 출력만으로 세밀하게 구분하지 않는다).

JSON 출력 스키마는 실제 claude/codex CLI를 설치해 이 저장소를 대상으로 직접 실행해 확인했다
(claude: result가 문자열, codex: item.completed/agent_message 이벤트) — 다만 방어적 파싱은
그대로 유지한다(CLI 버전에 따라 스키마가 바뀔 수 있으니 알려진 경로가 다 실패해도 raw
출력을 그대로 돌려준다).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .protocol import now_iso_kst

# Claude Code 쪽에 파일 변경 도구를 아예 안 주는 것 — 프롬프트가 뭐라고 하든 구조적으로 차단.
_CLAUDE_DISALLOWED_TOOLS = "Write,Edit,Bash"
# 10이었을 때 실측 3회 중 1회꼴로 error_max_turns 실패 — Bash가 막혀 있으니 모델이 몇 번
# 시도하다 거부당하고 Glob/Read로 전환하는 데 턴을 더 쓴다. 20으로 올려 그 여유를 준다.
_MAX_TURNS = 20
_DEFAULT_TIMEOUT_S = 300


class CodeAdapterNotConfiguredError(Exception):
    """CLI 자체가 PATH에 없음 — 설치 안 됨."""


class CodeAdapterError(Exception):
    """CLI는 있지만 실행이 실패함 — 인증 안 됨·타임아웃·비정상 종료 등 원인 불문."""


def claude_code_available() -> bool:
    return shutil.which("claude") is not None


def codex_available() -> bool:
    return shutil.which("codex") is not None


def _parse_claude_code_output(stdout: str) -> tuple[str, Optional[int]]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip(), None
    if not isinstance(data, dict):
        return stdout.strip(), None

    result = data.get("result")
    if isinstance(result, str) and result:
        return result, data.get("num_turns")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            if texts:
                return "\n".join(texts), data.get("num_turns")

    # 알려진 스키마 어디에도 안 맞으면 최소한 뭔가는 돌려준다.
    return json.dumps(data, ensure_ascii=False), data.get("num_turns")


def run_claude_code_analysis(workspace_path: Path, query: str, timeout_s: int = _DEFAULT_TIMEOUT_S) -> dict:
    if not claude_code_available():
        raise CodeAdapterNotConfiguredError("claude CLI가 PATH에 없습니다")

    started = time.monotonic()
    args = [
        "claude",
        "-p",
        query,
        "--output-format",
        "json",
        "--disallowed-tools",
        _CLAUDE_DISALLOWED_TOOLS,
        "--max-turns",
        str(_MAX_TURNS),
    ]
    try:
        proc = subprocess.run(args, cwd=str(workspace_path), capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError as e:
        raise CodeAdapterNotConfiguredError(str(e)) from e
    except subprocess.TimeoutExpired as e:
        raise CodeAdapterError(f"시간 초과({timeout_s}초)") from e

    duration_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode != 0:
        raise CodeAdapterError((proc.stderr or proc.stdout or f"종료 코드 {proc.returncode}")[:2000])

    summary, turns = _parse_claude_code_output(proc.stdout)
    return {
        "codeAdapter": "CLAUDE_CODE",
        "summary": summary,
        "turns": turns,
        "durationMs": duration_ms,
        "collectedAt": now_iso_kst(),
    }


def _parse_codex_event_line(line: str) -> Optional[str]:
    """한 줄이 완료된 agent_message 이벤트면 그 텍스트를, 아니면 None을 돌려준다."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or event.get("type") != "item.completed":
        return None
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


def run_codex_analysis(
    workspace_path: Path,
    query: str,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    on_turn_complete: Optional[Callable[[int], None]] = None,
) -> dict:
    """codex exec을 실행한다. `--json`이 NDJSON을 한 줄씩 찍어주므로 Popen으로 스트리밍
    읽어 턴이 끝날 때마다(item.completed/agent_message) on_turn_complete를 즉시 호출한다 —
    타이머 기반 추정치보다 실제 진행 상황에 가까운 신호를 제공하기 위함(agent.py의
    _progress_ticker와 별개로, CODEX 어댑터에서만 추가로 쓰인다).
    """
    if not codex_available():
        raise CodeAdapterNotConfiguredError("codex CLI가 PATH에 없습니다")

    started = time.monotonic()
    # git 저장소가 아닌 DIRECTORY 워크스페이스(ProjectWorkspaceConfig.workspace_type)에서는
    # 이 플래그가 없으면 codex CLI가 "Not inside a trusted directory"로 거부한다.
    args = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--json", query]
    try:
        proc = subprocess.Popen(
            args, cwd=str(workspace_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
        )
    except FileNotFoundError as e:
        raise CodeAdapterNotConfiguredError(str(e)) from e

    # subprocess.run(timeout=...)와 달리 스트리밍 읽기(for line in proc.stdout)는 그 자체로
    # 시간 제한이 없다 — CLI가 멈추면 루프가 그냥 무한 대기한다. 별도 타이머로 강제 종료한다.
    timed_out = threading.Event()

    def _kill_on_timeout() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout_s, _kill_on_timeout)
    timer.start()

    stdout_lines: list[str] = []
    last_message: Optional[str] = None
    turn_count = 0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_lines.append(line)
            text = _parse_codex_event_line(line)
            if text is None:
                continue
            last_message = text
            turn_count += 1
            if on_turn_complete is not None:
                on_turn_complete(turn_count)
        proc.wait()
    finally:
        timer.cancel()

    if timed_out.is_set():
        raise CodeAdapterError(f"시간 초과({timeout_s}초)")

    duration_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode != 0:
        stderr_output = proc.stderr.read() if proc.stderr else ""
        raise CodeAdapterError((stderr_output or "".join(stdout_lines) or f"종료 코드 {proc.returncode}")[:2000])

    summary = last_message if last_message is not None else "".join(stdout_lines).strip()
    return {
        "codeAdapter": "CODEX",
        "summary": summary,
        "turns": turn_count if last_message is not None else None,
        "durationMs": duration_ms,
        "collectedAt": now_iso_kst(),
    }


AVAILABILITY_CHECKS = {
    "CLAUDE_CODE": claude_code_available,
    "CODEX": codex_available,
}

RUNNERS = {
    "CLAUDE_CODE": run_claude_code_analysis,
    "CODEX": run_codex_analysis,
}


@dataclass
class ProjectWorkspaceConfig:
    """등록된 프로젝트 폴더 — searchFolders(FILE_SEARCH)와 같은 이유로 등록 UI는 아직 없고
    지금은 정적 설정만 지원한다. workspace_type·available_code_adapters는 직접 안 채우고
    from_root_path()로 자동 판정하는 걸 기본으로 쓴다.
    """

    workspace_id: str
    display_name: str
    root_path: str
    workspace_type: str  # "GIT_REPOSITORY" | "DIRECTORY"
    available_code_adapters: list[str] = field(default_factory=list)  # ["CLAUDE_CODE", "CODEX"]의 부분집합

    @staticmethod
    def from_root_path(workspace_id: str, display_name: str, root_path: str) -> "ProjectWorkspaceConfig":
        workspace_type = "GIT_REPOSITORY" if (Path(root_path) / ".git").exists() else "DIRECTORY"
        adapters = [name for name, check in AVAILABILITY_CHECKS.items() if check()]
        return ProjectWorkspaceConfig(
            workspace_id=workspace_id,
            display_name=display_name,
            root_path=root_path,
            workspace_type=workspace_type,
            available_code_adapters=adapters,
        )
