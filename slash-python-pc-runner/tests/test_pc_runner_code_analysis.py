"""CODE_ANALYSIS가 agent.py를 실제로 거쳐 TASK→RESULT 왕복까지 이어지는지 확인.
CLI 실행/파싱 로직 자체는 test_code_adapters.py에서 다룬다 — 여기선 어댑터 실행 함수를
몽키패치해 실제 claude/codex CLI 의존 없이 validate/execute 배선만 검증한다.
"""

import json
import uuid

import pytest

import slash_pc_runner.agent as agent_module
from slash_pc_runner.agent import ContractPcRunner, ContractPcRunnerOptions
from slash_pc_runner.code_adapters import ProjectWorkspaceConfig

from fake_pc_runner_server import start_fake_pc_runner_server


@pytest.fixture
def server():
    s = start_fake_pc_runner_server()
    yield s
    s.close()


def start_agent(server, **options_kwargs) -> ContractPcRunner:
    agent = ContractPcRunner(
        ContractPcRunnerOptions(api_base_url=server.url, pairing_code="000000", heartbeat_interval_s=60, **options_kwargs)
    )
    agent.start()
    agent.wait_until_ready()
    return agent


def make_workspace(tmp_path, adapters=("CLAUDE_CODE",)) -> ProjectWorkspaceConfig:
    return ProjectWorkspaceConfig(
        workspace_id="w1",
        display_name="테스트 프로젝트",
        root_path=str(tmp_path),
        workspace_type="DIRECTORY",
        available_code_adapters=list(adapters),
    )


def test_reports_project_workspaces_in_ready(server, tmp_path):
    workspace = make_workspace(tmp_path, adapters=["CLAUDE_CODE", "CODEX"])
    agent = start_agent(server, project_workspaces=[workspace])
    try:
        ready = server.wait_for_message("READY")
    finally:
        agent.stop()
    assert ready["projectWorkspaces"] == [
        {
            "workspaceId": "w1",
            "displayName": "테스트 프로젝트",
            "workspaceType": "DIRECTORY",
            "availableCodeAdapters": ["CLAUDE_CODE", "CODEX"],
        }
    ]


def test_succeeds_with_configured_adapter(server, tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path, adapters=["CLAUDE_CODE"])
    monkeypatch.setitem(
        agent_module.CODE_ADAPTER_AVAILABILITY_CHECKS,
        "CLAUDE_CODE",
        lambda: True,
    )
    monkeypatch.setitem(
        agent_module.CODE_ADAPTER_RUNNERS,
        "CLAUDE_CODE",
        lambda root_path, query: {"codeAdapter": "CLAUDE_CODE", "summary": f"[{root_path}] {query}", "turns": 1, "durationMs": 10, "collectedAt": "now"},
    )

    agent = start_agent(server, project_workspaces=[workspace])
    try:
        server.send_task(
            str(uuid.uuid4()), str(uuid.uuid4()), "CODE_ANALYSIS", {"workspaceId": "w1", "query": "구조 설명해줘"}
        )
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is True
        result = server.wait_for_message("RESULT")
        assert result["status"] == "SUCCEEDED"
        assert result["result"]["codeAdapter"] == "CLAUDE_CODE"
        assert "구조 설명해줘" in result["result"]["summary"]
    finally:
        agent.stop()


def test_rejects_unknown_workspace(server):
    agent = start_agent(server)
    try:
        server.send_task(
            str(uuid.uuid4()), str(uuid.uuid4()), "CODE_ANALYSIS", {"workspaceId": "no-such-workspace", "query": "설명"}
        )
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is False
        assert ack["reasonCode"] == "WORKSPACE_NOT_FOUND"
    finally:
        agent.stop()


def test_rejects_workspace_with_no_available_adapter(server, tmp_path):
    workspace = make_workspace(tmp_path, adapters=[])
    agent = start_agent(server, project_workspaces=[workspace])
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "CODE_ANALYSIS", {"workspaceId": "w1", "query": "설명"})
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is False
        assert ack["reasonCode"] == "CODE_AGENT_NOT_CONFIGURED"
    finally:
        agent.stop()


def test_rejects_requested_adapter_not_in_workspace(server, tmp_path):
    workspace = make_workspace(tmp_path, adapters=["CLAUDE_CODE"])
    agent = start_agent(server, project_workspaces=[workspace])
    try:
        server.send_task(
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            "CODE_ANALYSIS",
            {"workspaceId": "w1", "query": "설명", "codeAdapter": "CODEX"},
        )
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is False
        assert ack["reasonCode"] == "CODE_AGENT_NOT_CONFIGURED"
    finally:
        agent.stop()


def test_codex_streams_turn_progress_via_wss(server, tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path, adapters=["CODEX"])
    monkeypatch.setitem(agent_module.CODE_ADAPTER_AVAILABILITY_CHECKS, "CODEX", lambda: True)

    def fake_codex_runner(root_path, query, on_turn_complete=None):
        assert on_turn_complete is not None
        on_turn_complete(1)
        on_turn_complete(2)
        return {"codeAdapter": "CODEX", "summary": "완료", "turns": 2, "durationMs": 10, "collectedAt": "now"}

    monkeypatch.setitem(agent_module.CODE_ADAPTER_RUNNERS, "CODEX", fake_codex_runner)

    agent = start_agent(server, project_workspaces=[workspace])
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "CODE_ANALYSIS", {"workspaceId": "w1", "query": "설명해줘"})
        server.wait_for_message("ACK")
        result = server.wait_for_message("RESULT")
        assert result["status"] == "SUCCEEDED"

        progress_percents = [m["percent"] for m in server.received_messages if m.get("type") == "PROGRESS"]
        # 초기 0%(작업 시작 시 고정 전송) 뒤로 턴 1·2 완료 시의 실시간 신호(10, 20)가 순서대로 온다.
        assert progress_percents == [0, 10, 20]
    finally:
        agent.stop()


class TestResultTruncation:
    def test_leaves_small_result_untouched(self):
        result = {"codeAdapter": "CLAUDE_CODE", "summary": "짧은 요약", "turns": 1, "durationMs": 10, "collectedAt": "now"}
        assert agent_module._truncate_code_analysis_result(result) == result

    def test_truncates_oversized_summary_within_byte_limit(self):
        huge_summary = "가나다라마바사아자차카" * 20000  # 각 문자 3바이트 — 넉넉히 상한 초과
        result = {"codeAdapter": "CLAUDE_CODE", "summary": huge_summary, "turns": 1, "durationMs": 10, "collectedAt": "now"}

        truncated = agent_module._truncate_code_analysis_result(result)

        encoded_size = len(json.dumps(truncated, ensure_ascii=False).encode("utf-8"))
        assert encoded_size <= agent_module.CODE_ANALYSIS_RESULT_JSON_BYTE_LIMIT
        assert truncated["truncated"] is True
        assert truncated["summary"].endswith(agent_module.CODE_ANALYSIS_TRUNCATION_MARKER)
        # 나머지 필드는 그대로 보존된다.
        assert truncated["turns"] == 1
        assert truncated["codeAdapter"] == "CLAUDE_CODE"

    def test_truncate_utf8_never_splits_multibyte_character(self):
        text = "한글" * 100
        for max_bytes in range(0, 30):
            truncated = agent_module._truncate_utf8(text, max_bytes)
            # 잘린 결과를 다시 인코딩해도 예외 없이 유효한 UTF-8이어야 한다.
            truncated.encode("utf-8")
            assert len(truncated.encode("utf-8")) <= max_bytes


def test_oversized_result_is_truncated_before_result_sent(server, tmp_path, monkeypatch):
    workspace = make_workspace(tmp_path, adapters=["CLAUDE_CODE"])
    monkeypatch.setitem(agent_module.CODE_ADAPTER_AVAILABILITY_CHECKS, "CLAUDE_CODE", lambda: True)

    huge_summary = "가나다라마바사아자차카" * 20000

    def fake_runner(root_path, query):
        return {"codeAdapter": "CLAUDE_CODE", "summary": huge_summary, "turns": 1, "durationMs": 10, "collectedAt": "now"}

    monkeypatch.setitem(agent_module.CODE_ADAPTER_RUNNERS, "CLAUDE_CODE", fake_runner)

    agent = start_agent(server, project_workspaces=[workspace])
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "CODE_ANALYSIS", {"workspaceId": "w1", "query": "설명"})
        server.wait_for_message("ACK")
        result = server.wait_for_message("RESULT")
        assert result["status"] == "SUCCEEDED"
        assert result["result"]["truncated"] is True
        encoded_size = len(json.dumps(result["result"], ensure_ascii=False).encode("utf-8"))
        assert encoded_size <= agent_module.CODE_ANALYSIS_RESULT_JSON_BYTE_LIMIT
    finally:
        agent.stop()


def test_execution_failure_maps_to_policy_denied(server, tmp_path, monkeypatch):
    from slash_pc_runner.code_adapters import CodeAdapterError

    workspace = make_workspace(tmp_path, adapters=["CLAUDE_CODE"])
    monkeypatch.setitem(agent_module.CODE_ADAPTER_AVAILABILITY_CHECKS, "CLAUDE_CODE", lambda: True)

    def failing_runner(root_path, query):
        raise CodeAdapterError("인증 안 됨")

    monkeypatch.setitem(agent_module.CODE_ADAPTER_RUNNERS, "CLAUDE_CODE", failing_runner)

    agent = start_agent(server, project_workspaces=[workspace])
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "CODE_ANALYSIS", {"workspaceId": "w1", "query": "설명"})
        server.wait_for_message("ACK")
        result = server.wait_for_message("RESULT")
        assert result["status"] == "FAILED"
        assert result["error"]["code"] == "POLICY_DENIED"
    finally:
        agent.stop()
