"""AI_AGENT_USAGE가 agent.py를 실제로 거쳐 TASK→RESULT 왕복까지 이어지는지 확인.
집계 로직 자체는 test_usage_adapters.py에서 다룬다.
"""

import json
import uuid
from pathlib import Path

import pytest

import slash_agent.usage_adapters as usage_adapters
from slash_agent.agent import ContractAgent, ContractAgentOptions

from fake_agent_server import start_fake_agent_server


@pytest.fixture
def server():
    s = start_fake_agent_server()
    yield s
    s.close()


def start_agent(server) -> ContractAgent:
    agent = ContractAgent(
        ContractAgentOptions(api_base_url=server.url, pairing_code="000000", heartbeat_interval_s=60)
    )
    agent.start()
    agent.wait_until_ready()
    return agent


def test_returns_usage_totals_for_configured_provider(server, tmp_path, monkeypatch):
    root = tmp_path / "claude-projects"
    session_path = root / "proj" / "session-1.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-01T10:00:00.000Z",
                "message": {"usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(usage_adapters, "_claude_code_root", lambda: root)

    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "AI_AGENT_USAGE", {"provider": "CLAUDE_CODE"})
        result = server.wait_for_message("RESULT")
        assert result["status"] == "SUCCEEDED"
        assert result["result"]["provider"] == "CLAUDE_CODE"
        assert result["result"]["totalInputTokens"] == 10
        assert result["result"]["totalOutputTokens"] == 20
    finally:
        agent.stop()


def test_rejects_unknown_provider(server):
    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "AI_AGENT_USAGE", {"provider": "GPT4_CLI"})
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is False
        assert ack["reasonCode"] == "INVALID_PARAMETERS"
    finally:
        agent.stop()


def test_rejects_provider_with_no_local_data(server, tmp_path, monkeypatch):
    monkeypatch.setattr(usage_adapters, "_codex_root", lambda: tmp_path / "does-not-exist")

    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "AI_AGENT_USAGE", {"provider": "CODEX"})
        ack = server.wait_for_message("ACK")
        assert ack["accepted"] is False
        assert ack["reasonCode"] == "CODE_AGENT_NOT_CONFIGURED"
    finally:
        agent.stop()
