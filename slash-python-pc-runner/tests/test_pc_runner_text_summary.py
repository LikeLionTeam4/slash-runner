"""TEXT_SUMMARY(RUNNER 실행 경로)가 agent.py를 실제로 거쳐 TASK→RESULT 왕복까지 이어지는지
확인. CLI 실행/파싱 로직 자체는 test_summary_adapters.py에서 다룬다 — 여기선 어댑터 실행
함수를 몽키패치해 실제 claude/codex CLI 의존 없이 validate/execute 배선만 검증한다.

slash-api가 아직 TEXT_SUMMARY를 LOCAL_AGENT로 라우팅하지 않아(ProcessingRoute.LLM_SERVICE
고정) 실제 서버가 이 taskType의 TASK를 보내는 일은 없다 — 이 시험은 라우팅이 붙기 전에
Runner 쪽 배선이 미리 올바른지 확인해 두는 것이다.
"""

import uuid

import pytest

import slash_pc_runner.agent as agent_module
from slash_pc_runner.agent import ContractPcRunner, ContractPcRunnerOptions

from fake_pc_runner_server import start_fake_pc_runner_server


@pytest.fixture
def server():
    s = start_fake_pc_runner_server()
    yield s
    s.close()


def start_agent(server) -> ContractPcRunner:
    agent = ContractPcRunner(
        ContractPcRunnerOptions(api_base_url=server.url, pairing_code="000000", heartbeat_interval_s=60)
    )
    agent.start()
    agent.wait_until_ready()
    return agent


def test_reports_available_summary_adapters_in_ready(server, monkeypatch):
    monkeypatch.setitem(agent_module.SUMMARY_ADAPTER_AVAILABILITY_CHECKS, "CLAUDE_CODE", lambda: True)
    monkeypatch.setitem(agent_module.SUMMARY_ADAPTER_AVAILABILITY_CHECKS, "CODEX", lambda: False)

    agent = start_agent(server)
    try:
        ready = server.wait_for_message("READY")
    finally:
        agent.stop()
    assert ready["availableSummaryAdapters"] == ["CLAUDE_CODE"]


def test_succeeds_with_configured_summary_adapter(server, monkeypatch):
    monkeypatch.setitem(agent_module.SUMMARY_ADAPTER_AVAILABILITY_CHECKS, "CLAUDE_CODE", lambda: True)
    monkeypatch.setitem(
        agent_module.SUMMARY_ADAPTER_RUNNERS,
        "CLAUDE_CODE",
        lambda text: {"summaryAdapter": "CLAUDE_CODE", "summary": f"요약: {text}", "durationMs": 10, "collectedAt": "now"},
    )

    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "TEXT_SUMMARY", {"text": "요약할 긴 글"})
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is True
        result = server.wait_for_message("RESULT")
        assert result["status"] == "SUCCEEDED"
        assert result["result"]["summaryAdapter"] == "CLAUDE_CODE"
        assert result["result"]["summary"] == "요약: 요약할 긴 글"
    finally:
        agent.stop()


def test_rejects_missing_text_parameter(server, monkeypatch):
    monkeypatch.setitem(agent_module.SUMMARY_ADAPTER_AVAILABILITY_CHECKS, "CLAUDE_CODE", lambda: True)

    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "TEXT_SUMMARY", {})
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is False
        assert ack["reasonCode"] == "INVALID_PARAMETERS"
    finally:
        agent.stop()


def test_rejects_when_no_summary_adapter_configured(server, monkeypatch):
    monkeypatch.setitem(agent_module.SUMMARY_ADAPTER_AVAILABILITY_CHECKS, "CLAUDE_CODE", lambda: False)
    monkeypatch.setitem(agent_module.SUMMARY_ADAPTER_AVAILABILITY_CHECKS, "CODEX", lambda: False)

    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "TEXT_SUMMARY", {"text": "요약할 긴 글"})
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is False
        assert ack["reasonCode"] == "CODE_AGENT_NOT_CONFIGURED"
    finally:
        agent.stop()


def test_oversized_summary_result_is_truncated(server, monkeypatch):
    monkeypatch.setitem(agent_module.SUMMARY_ADAPTER_AVAILABILITY_CHECKS, "CLAUDE_CODE", lambda: True)
    huge_summary = "가나다라마바사아자차카" * 20000
    monkeypatch.setitem(
        agent_module.SUMMARY_ADAPTER_RUNNERS,
        "CLAUDE_CODE",
        lambda text: {"summaryAdapter": "CLAUDE_CODE", "summary": huge_summary, "durationMs": 10, "collectedAt": "now"},
    )

    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "TEXT_SUMMARY", {"text": "요약할 긴 글"})
        server.wait_for_message("ACK")
        result = server.wait_for_message("RESULT")
        assert result["status"] == "SUCCEEDED"
        assert result["result"]["truncated"] is True
    finally:
        agent.stop()
