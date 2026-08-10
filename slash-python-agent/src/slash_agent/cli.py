"""개발용 CLI 진입점 — cli.ts 대응. `python -m slash_agent.cli`로 실행."""

from __future__ import annotations

import json
import os
import signal
import sys
import urllib.request
from pathlib import Path


def _log(line: str) -> None:
    # 파일/파이프로 리다이렉트되면 표준출력이 완전 버퍼링돼 프로세스 종료 전까지 안 보인다 —
    # 로그는 실시간으로 확인해야 하니 매번 강제로 흘려보낸다.
    print(line, flush=True)

from .agent import ContractAgent, ContractAgentOptions
from .identity_store import KeyringIdentityStore
from .processed_task_store import JsonFileProcessedTaskStore

STATE_DIR = Path.home() / ".slash-agent-py"


def _obtain_pairing_code(api_base_url: str) -> str:
    explicit = os.environ.get("SLASH_AGENT_PAIRING_CODE")
    if explicit:
        return explicit

    login_req = urllib.request.Request(
        f"{api_base_url}/test/login",
        data=json.dumps({"email": "slash-agent-py-tester@example.com", "displayName": "slash-agent-py tester"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(login_req) as res:
        token = json.loads(res.read().decode())["token"]

    pairing_req = urllib.request.Request(
        f"{api_base_url}/api/v1/pairing-requests",
        data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(pairing_req) as res:
        pairing_code = json.loads(res.read().decode())["data"]["pairingCode"]
    _log(f"[slash-agent] 자동 발급된 페어링 코드: {pairing_code}")
    return pairing_code


def main() -> None:
    api_base_url = os.environ.get("SLASH_AGENT_API_BASE_URL", "http://localhost:4000")
    identity_store = KeyringIdentityStore(service_name="slash-agent-py-dev")
    processed_task_store = JsonFileProcessedTaskStore(STATE_DIR / "processed-tasks.json")

    has_persisted_identity = identity_store.load() is not None
    pairing_code = None if has_persisted_identity else _obtain_pairing_code(api_base_url)

    agent = ContractAgent(
        ContractAgentOptions(
            api_base_url=api_base_url,
            pairing_code=pairing_code,
            device_name=os.environ.get("SLASH_AGENT_DEVICE_NAME", "slash-agent-py-simulator"),
            heartbeat_interval_s=float(os.environ.get("SLASH_AGENT_HEARTBEAT_INTERVAL_S", "30")),
            log=_log,
            identity_store=identity_store,
            processed_task_store=processed_task_store,
        )
    )

    agent.start()
    agent.wait_until_ready(20.0)
    _log(f"[slash-agent] READY (deviceId={agent.get_device_id()})")

    def _shutdown(signum, frame):
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.pause()


if __name__ == "__main__":
    main()
