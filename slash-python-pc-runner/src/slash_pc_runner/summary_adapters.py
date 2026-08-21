"""SummaryAdapter — TEXT_SUMMARY의 Runner 실행 경로(RUN-02~05, 클라우드 LLM 제거 계획
`slash-docs#3` 참고). code_adapters.py(CODE_ANALYSIS)와 실행 방식이 다르다:

- CODE_ANALYSIS는 등록된 프로젝트 폴더를 읽어야 해서 그 폴더를 cwd로 CLI를 실행하지만,
  이 어댑터는 사용자가 붙여넣은 텍스트 하나를 요약하는 게 전부라 **어떤 파일에도 접근할
  이유가 없다** — 매 실행마다 비어 있는 임시 디렉터리를 만들어 그 안에서 실행하고 끝나면
  지운다.
- 텍스트는 CLI 인자가 아니라 표준입력(stdin)으로 넘긴다 — 인자로 넘기면 OS의 명령줄 길이
  제한에 걸릴 수 있고, 프로세스 목록(`ps`)에 원문이 그대로 노출된다. 표준입력은 둘 다
  피한다.
- 파일 쓰기·Shell 실행·MCP·웹 검색까지 CODE_ANALYSIS보다 더 엄격하게 전부 차단한다 —
  요약은 텍스트를 읽고 텍스트로 답하는 것 외에 어떤 도구도 쓸 이유가 없다.

인증은 code_adapters.py와 동일하게 로컬 CLI 로그인 상태를 그대로 쓴다(별도 처리 없음).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from .code_adapters import claude_code_available, codex_available
from .protocol import now_iso_kst

# 요약 대상 텍스트는 NLU가 이미 최소 150자(공백 제외)를 보장하지만, 상한은 별도로 없다 —
# 여기서 상한을 두지 않으면 사용자가 8000자(TEXT_SUMMARY 계약상 최대치, slash-nlu
# SUMMARY_MAX_INPUT_CHARS와 동일)에 가까운 글을 보낼 때마다 CLI 호출 시간·비용이 늘어난다.
SUMMARY_MAX_INPUT_CHARS = 8000

# 요약은 CODE_ANALYSIS(최대 300초)와 달리 파일 탐색이 없는 단일 턴 작업이라 훨씬 짧게 끝나야
# 정상이다 — 타임아웃을 짧게 잡아 CLI가 예상외로 도구 사용을 시도하다 막혀 오래 걸리는
# 상황(RUN-02가 요구하는 "도구 비활성 모드")을 빨리 실패로 전환한다.
_DEFAULT_TIMEOUT_S = 60

_CLAUDE_DISALLOWED_TOOLS = "Write,Edit,Bash,WebSearch,WebFetch,NotebookEdit,Task"


class SummaryAdapterNotConfiguredError(Exception):
    """CLI 자체가 PATH에 없음 — 설치 안 됨."""


class SummaryAdapterError(Exception):
    """CLI는 있지만 실행이 실패함 — 타임아웃·비정상 종료 등 원인 불문."""


def _validate_input_length(text: str) -> None:
    if len(text) > SUMMARY_MAX_INPUT_CHARS:
        raise SummaryAdapterError(f"입력이 너무 깁니다({len(text)}자, 최대 {SUMMARY_MAX_INPUT_CHARS}자)")


def _build_prompt(text: str) -> str:
    return (
        "다음 글을 한국어 3문장 이내로 요약해줘. 파일을 만들거나 수정하지 말고 텍스트로만 답해.\n\n"
        f"{text}"
    )


def _extract_claude_result_text(stdout: str) -> str:
    # code_adapters._parse_claude_code_output()과 같은 스키마(claude -p --output-format
    # json의 result 필드)지만, 요약은 turns·usage를 결과에 안 실으므로 텍스트만 뽑는 더
    # 단순한 버전을 따로 둔다.
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip()
    if not isinstance(data, dict):
        return stdout.strip()
    result = data.get("result")
    if isinstance(result, str) and result:
        return result
    return stdout.strip()


def run_claude_code_summary(text: str, timeout_s: int = _DEFAULT_TIMEOUT_S) -> dict:
    if not claude_code_available():
        raise SummaryAdapterNotConfiguredError("claude CLI가 PATH에 없습니다")
    _validate_input_length(text)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="slash-summary-") as empty_workspace:
        args = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--disallowed-tools",
            _CLAUDE_DISALLOWED_TOOLS,
        ]
        try:
            proc = subprocess.run(
                args,
                cwd=empty_workspace,
                input=_build_prompt(text),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except FileNotFoundError as e:
            raise SummaryAdapterNotConfiguredError(str(e)) from e
        except subprocess.TimeoutExpired as e:
            raise SummaryAdapterError(f"시간 초과({timeout_s}초)") from e

    duration_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode != 0:
        # stderr에 원문 요약 대상 텍스트가 그대로 반영될 가능성은 낮지만(CLI 자체 오류가
        # 대부분), 방어적으로 code_adapters.py와 같은 2000자 상한을 둔다.
        raise SummaryAdapterError((proc.stderr or proc.stdout or f"종료 코드 {proc.returncode}")[:2000])

    return {
        "summaryAdapter": "CLAUDE_CODE",
        "summary": _extract_claude_result_text(proc.stdout),
        "durationMs": duration_ms,
        "collectedAt": now_iso_kst(),
    }


def _extract_codex_result_text(stdout: str) -> Optional[str]:
    # code_adapters._parse_codex_event_line()과 동일한 이벤트 스키마 — 마지막
    # item.completed/agent_message의 text가 최종 답변이다.
    last_message: Optional[str] = None
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                last_message = text
    return last_message


def run_codex_summary(text: str, timeout_s: int = _DEFAULT_TIMEOUT_S) -> dict:
    if not codex_available():
        raise SummaryAdapterNotConfiguredError("codex CLI가 PATH에 없습니다")
    _validate_input_length(text)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="slash-summary-") as empty_workspace:
        # 임시 디렉터리는 git 저장소가 아니므로 --skip-git-repo-check가 필요하다
        # (code_adapters.py의 DIRECTORY 워크스페이스와 동일한 이유).
        args = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--json"]
        try:
            proc = subprocess.run(
                args,
                cwd=empty_workspace,
                input=_build_prompt(text),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except FileNotFoundError as e:
            raise SummaryAdapterNotConfiguredError(str(e)) from e
        except subprocess.TimeoutExpired as e:
            raise SummaryAdapterError(f"시간 초과({timeout_s}초)") from e

    duration_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode != 0:
        raise SummaryAdapterError((proc.stderr or proc.stdout or f"종료 코드 {proc.returncode}")[:2000])

    summary = _extract_codex_result_text(proc.stdout)
    if summary is None:
        raise SummaryAdapterError("codex 응답에서 요약 결과를 찾을 수 없습니다")

    return {
        "summaryAdapter": "CODEX",
        "summary": summary,
        "durationMs": duration_ms,
        "collectedAt": now_iso_kst(),
    }


AVAILABILITY_CHECKS = {
    "CLAUDE_CODE": claude_code_available,
    "CODEX": codex_available,
}

RUNNERS = {
    "CLAUDE_CODE": run_claude_code_summary,
    "CODEX": run_codex_summary,
}
