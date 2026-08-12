"""CODE_ANALYSIS가 agent.py를 실제로 거쳐 TASK→RESULT 왕복까지 이어지는지 확인.
CLI 실행/파싱 로직 자체는 test_code_adapters.py에서 다룬다 — 여기선 어댑터 실행 함수를
몽키패치해 실제 claude/codex CLI 의존 없이 validate/execute 배선만 검증한다.
"""

import uuid

import pytest

import slash_agent.agent as agent_module
from slash_agent.agent import ContractAgent, ContractAgentOptions
from slash_agent.code_adapters import ProjectWorkspaceConfig

from fake_agent_server import start_fake_agent_server


@pytest.fixture
def server():
    s = start_fake_agent_server()
    yield s
    s.close()


def start_agent(server, **options_kwargs) -> ContractAgent:
    agent = ContractAgent(
        ContractAgentOptions(api_base_url=server.url, pairing_code="000000", heartbeat_interval_s=60, **options_kwargs)
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


def test_execution_failure_maps_to_policy_denied(server, tmp_path, monkeypatch):
    from slash_agent.code_adapters import CodeAdapterError

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
