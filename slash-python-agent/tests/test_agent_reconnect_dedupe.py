"""agent.reconnect-dedupe.test.ts 대응 — 재연결·중복 Task 단위 시험.

실제 소켓·서명 검증을 쓰는 서버(fake_agent_server)로 agent.py를 직접 구동한다.
"""

import time
import uuid

import pytest

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


def test_duplicate_task_resends_cached_result_without_rerun(server):
    agent = start_agent(server)
    try:
        task_id = str(uuid.uuid4())
        dispatch_id = str(uuid.uuid4())

        server.send_task(task_id, dispatch_id, "SYSTEM_STATUS", {})
        first_result = server.wait_for_message("RESULT")

        server.send_task(task_id, dispatch_id, "SYSTEM_STATUS", {})
        second_result = server.wait_for_message("RESULT")

        # 재실행 시 finishedAt 갱신, 캐시 재전송 시 완전 동일
        assert second_result["finishedAt"] == first_result["finishedAt"]
        assert second_result["result"] == first_result["result"]
    finally:
        agent.stop()


def test_resends_unacked_result_after_reconnect(server):
    server.auto_ack_result = False  # RESULT_ACK를 보내지 않아 "미완료" 상태를 만든다
    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "SYSTEM_STATUS", {})
        server.wait_for_message("RESULT")

        since_index = len(server.received_messages)
        server.disconnect_agent()

        resent = server.wait_for_message("RESULT", timeout_s=10, since_index=since_index)
        assert resent
    finally:
        agent.stop()


def test_does_not_resend_after_result_ack(server):
    server.auto_ack_result = True
    agent = start_agent(server)
    try:
        server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "SYSTEM_STATUS", {})
        server.wait_for_message("RESULT")
        # RESULT_ACK: 서버 발신 메시지라 관측 불가 — 처리 대기 시간만 부여
        time.sleep(0.2)

        ready_before = server.ready_count
        since_index = len(server.received_messages)
        server.disconnect_agent()

        # 재연결(READY 재도달) 선행 확인 — "재전송 안 함" 검증의 전제조건
        server.wait_for_message("HELLO", timeout_s=10, since_index=since_index)
        deadline = time.monotonic() + 10
        while server.ready_count <= ready_before:
            if time.monotonic() > deadline:
                raise TimeoutError("재연결 시간 초과")
            time.sleep(0.1)

        with pytest.raises(TimeoutError):
            server.wait_for_message("RESULT", timeout_s=2, since_index=since_index)
    finally:
        agent.stop()
