"""agent.pairing.test.ts 대응 — 등록(페어링) 실패·재페어링 폴백 경로.

정상 페어링·재연결·중복방지는 test_agent_reconnect_dedupe.py에서 다룬다.
"""

import pytest

from slash_pc_runner.agent import ContractPcRunner, ContractPcRunnerOptions
from slash_pc_runner.crypto import generate_agent_key_pair
from slash_pc_runner.identity_store import PersistedAgentIdentity

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
