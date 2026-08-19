"""agent.pairing.test.ts 대응 — 등록(페어링) 실패·재페어링 폴백 경로.

정상 페어링·재연결·중복방지는 test_agent_reconnect_dedupe.py에서 다룬다.
"""

import pytest

from slash_pc_runner.agent import ContractPcRunner, ContractPcRunnerOptions
from slash_pc_runner.crypto import generate_agent_key_pair
from slash_pc_runner.identity_store import PersistedAgentIdentity
from slash_pc_runner.pairing_client import DeviceRevokedError

from fake_pc_runner_server import start_fake_pc_runner_server


class MemoryIdentityStore:
    def __init__(self, initial=None):
        self.current = initial
        self.saved: list[PersistedAgentIdentity] = []

    def load(self):
        return self.current

    def save(self, identity: PersistedAgentIdentity) -> None:
        self.current = identity
        self.saved.append(identity)

    def clear(self) -> None:
        self.current = None


@pytest.fixture
def server():
    s = start_fake_pc_runner_server()
    yield s
    s.close()


def test_invalid_pairing_code_fails_start(server):
    server.accepted_pairing_code = "111111"
    agent = ContractPcRunner(
        ContractPcRunnerOptions(
            api_base_url=server.url,
            pairing_code="000000",  # 서버가 받아주지 않는 코드
            heartbeat_interval_s=60,
        )
    )
    with pytest.raises(RuntimeError, match="PAIRING_CODE_INVALID"):
        agent.start()


def test_stale_identity_falls_back_to_repairing(server):
    server.accepted_pairing_code = "222222"

    # 서버 미인지 deviceId·deviceToken 사전 저장 상태 재현 — 키는 실제 유효한 Ed25519
    stale_key_pair = generate_agent_key_pair()
    identity_store = MemoryIdentityStore(
        PersistedAgentIdentity(
            device_id="stale-device-id",
            device_token="stale-token",
            private_key_pem=stale_key_pair.export_private_key_pem(),
            public_key_base64=stale_key_pair.public_key_base64,
        )
    )

    agent = ContractPcRunner(
        ContractPcRunnerOptions(
            api_base_url=server.url,
            pairing_code="222222",  # 갱신 실패 시 폴백용
            heartbeat_interval_s=60,
            identity_store=identity_store,
        )
    )
    agent.start()
    agent.wait_until_ready()
    try:
        assert agent.get_device_id() != "stale-device-id"
        assert len(identity_store.saved) > 0
        assert identity_store.saved[-1].device_id == agent.get_device_id()
    finally:
        agent.stop()


def test_revoked_device_does_not_fall_back_to_repairing(server):
    """slash-api#37(2026-08-18) 기준 — REST 토큰 갱신도 DEVICE_REVOKED를 구분해 응답한다.
    재페어링 시도는 해제를 무시하고 새 기기로 재등록해버리는 꼴이라, 시도 자체를 하면 안 된다.
    """
    server.accepted_pairing_code = "444444"
    pairing_agent = ContractPcRunner(
        ContractPcRunnerOptions(api_base_url=server.url, pairing_code="444444", heartbeat_interval_s=60)
    )
    pairing_agent.start()
    pairing_agent.wait_until_ready()
    device_id = pairing_agent.get_device_id()
    pairing_agent.stop()

    server.devices[device_id]["revoked"] = True

    # 서명이 유효한지는 상관없다 — 가짜 서버도 실제 서버(PairingService.refresh)도 해제 여부를
    # 서명 검증보다 먼저 확인한다.
    stale_key_pair = generate_agent_key_pair()
    identity_store = MemoryIdentityStore(
        PersistedAgentIdentity(
            device_id=device_id,
            device_token="any-token",
            private_key_pem=stale_key_pair.export_private_key_pem(),
            public_key_base64=stale_key_pair.public_key_base64,
        )
    )

    agent = ContractPcRunner(
        ContractPcRunnerOptions(
            api_base_url=server.url,
            pairing_code=None,  # 실제 재시작 시나리오 — 새 페어링 코드가 없다
            heartbeat_interval_s=60,
            identity_store=identity_store,
        )
    )
    with pytest.raises(DeviceRevokedError):
        agent.start()

    # 재페어링을 시도했다면 identity_store.saved에 새 기기 정보가 쌓였을 것이다.
    assert identity_store.saved == []
    assert identity_store.current is None
