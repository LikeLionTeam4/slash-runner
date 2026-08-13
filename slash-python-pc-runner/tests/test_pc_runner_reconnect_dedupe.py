"""agent.reconnect-dedupe.test.ts 대응 — 재연결·중복 Task 단위 시험.

실제 소켓·서명 검증을 쓰는 서버(fake_pc_runner_server)로 agent.py를 직접 구동한다.
"""

import time
import uuid

import pytest

from slash_pc_runner.agent import ContractPcRunner, ContractPcRunnerOptions
from slash_pc_runner.identity_store import PersistedAgentIdentity

from fake_pc_runner_server import start_fake_pc_runner_server


class MemoryIdentityStore:
    def __init__(self, initial=None):
        self.current = initial

    def load(self):
        return self.current

    def save(self, identity: PersistedAgentIdentity) -> None:
        self.current = identity

    def clear(self) -> None:
        self.current = None


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


def test_device_revoked_stops_agent_and_clears_identity(server):
    """slash-agent#16 — PROTOCOL_ERROR(DEVICE_REVOKED) 수신 시 재연결 중단 + identity 정리."""
    identity_store = MemoryIdentityStore()
    agent = ContractPcRunner(
        ContractPcRunnerOptions(
            api_base_url=server.url,
            pairing_code="000000",
            heartbeat_interval_s=60,
            identity_store=identity_store,
        )
    )
    agent.start()
    agent.wait_until_ready()
    try:
        assert identity_store.current is not None  # 정상 페어링 후 저장돼 있어야 "정리됨"을 구분 가능

        server.send_protocol_error("DEVICE_REVOKED", "device revoked")

        deadline = time.monotonic() + 5
        while agent.get_state() != "STOPPED":
            if time.monotonic() > deadline:
                raise TimeoutError("DEVICE_REVOKED 처리 대기 시간 초과")
            time.sleep(0.05)

        assert identity_store.current is None
    finally:
        agent.stop()


def test_device_revoked_processed_even_when_server_closes_immediately_after(server):
    """PR #18 리뷰(kimkangchan) 지적 — 실제 slash-api(#27)는 DEVICE_REVOKED 프레임을 보낸
    직후 곧바로 소켓을 닫는다(sendAndClose). 데이터 프레임과 close 프레임이 근접해 도착해도
    websockets 클라이언트가 recv()로 그 메시지를 먼저 넘겨주는지 확인한다 — 만약 close가
    먼저 처리되어 메시지가 유실되면 identity_store.clear()가 호출되지 않는다.
    """
    identity_store = MemoryIdentityStore()
    agent = ContractPcRunner(
        ContractPcRunnerOptions(
            api_base_url=server.url,
            pairing_code="000000",
            heartbeat_interval_s=60,
            identity_store=identity_store,
        )
    )
    agent.start()
    agent.wait_until_ready()
    try:
        assert identity_store.current is not None

        server.send_protocol_error("DEVICE_REVOKED", "device revoked", close_after=True)

        deadline = time.monotonic() + 5
        while agent.get_state() != "STOPPED":
            if time.monotonic() > deadline:
                raise TimeoutError("DEVICE_REVOKED 처리 대기 시간 초과 (close_after=True)")
            time.sleep(0.05)

        assert identity_store.current is None
    finally:
        agent.stop()


def test_reconnect_backoff_grows_when_ready_never_reached(server):
    """slash-api#26 — 소켓 연결(101) 직후가 아니라 READY 도달 후에만 백오프를 리셋해야 한다.

    해제된 기기처럼 인증 단계(HELLO 이후)에서 매번 거부당하면, 재시도 간격이 항상 1초로
    고정되지 않고 실제로 늘어나야 한다.
    """
    server.reject_hello = True
    agent = ContractPcRunner(
        ContractPcRunnerOptions(api_base_url=server.url, pairing_code="000000", heartbeat_interval_s=60)
    )
    agent.start()
    try:
        server.wait_for_message("HELLO", timeout_s=5)
        t1 = time.monotonic()
        server.wait_for_message("HELLO", timeout_s=5, since_index=1)
        t2 = time.monotonic()
        server.wait_for_message("HELLO", timeout_s=10, since_index=2)
        t3 = time.monotonic()

        first_gap = t2 - t1  # 1번째 실패 후 대기 — attempt=0 → 약 1초
        second_gap = t3 - t2  # 2번째 실패 후 대기 — attempt=1 → 약 2초(리셋됐다면 다시 약 1초)

        assert first_gap < 1.8
        assert second_gap > first_gap
    finally:
        agent.stop()
