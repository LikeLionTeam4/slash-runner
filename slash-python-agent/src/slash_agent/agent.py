"""로컬 에이전트 핵심 — agent.ts(ContractAgent) 대응.

TS는 단일 이벤트루프 기반이라 async/await로 동시성을 표현하지만, Python은 동기 WSS
클라이언트(websockets.sync.client)를 쓰므로 연결 루프를 백그라운드 스레드로 돌린다.
그 스레드 안에서만 소켓·resultCache를 건드리므로 별도 락은 두지 않았다.
"""

from __future__ import annotations

import json
import platform
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import websockets.sync.client as ws_client
from websockets.exceptions import ConnectionClosed

from .crypto import AgentKeyPair, generate_agent_key_pair, restore_agent_key_pair
from .identity_store import AgentIdentityStore, PersistedAgentIdentity
from .pairing_client import pair_agent, refresh_session, verify_pairing
from .processed_task_store import ProcessedTaskStore
from .protocol import (
    build_challenge_signing_payload,
    build_refresh_signing_payload,
    envelope,
    now_iso_kst,
)
from .system_status import collect_system_status

# Phase B에서 FILE_SEARCH가 추가된다.
SUPPORTED_TASK_TYPES: tuple[str, ...] = ("SYSTEM_STATUS",)

# RESULT_ACK 수신 후 재수신 대비 보관 기간
PROCESSED_TASK_RETENTION_S = 60 * 60


def _iso_to_epoch(iso_str: str) -> float:
    return datetime.fromisoformat(iso_str).timestamp()


@dataclass
class ContractAgentOptions:
    api_base_url: str
    # 정상 페어링 경로. preset_device_id/preset_device_token을 주면 이 값은 무시되고 HTTP 페어링을 건너뛴다.
    pairing_code: Optional[str] = None
    device_name: str = "slash-agent-py"
    heartbeat_interval_s: float = 30.0
    log: Callable[[str], None] = field(default=lambda line: None)
    # 시험 전용: 이미 발급된 deviceId/deviceToken을 직접 주입해 HTTP 페어링 단계를 생략한다.
    preset_device_id: Optional[str] = None
    preset_device_token: Optional[str] = None
    identity_store: Optional[AgentIdentityStore] = None
    processed_task_store: Optional[ProcessedTaskStore] = None


class ContractAgent:
    def __init__(self, options: ContractAgentOptions):
        self._options = options
        self._key_pair: AgentKeyPair = generate_agent_key_pair()
        self._socket = None
        self._device_id: Optional[str] = options.preset_device_id
        self._device_token: Optional[str] = options.preset_device_token
        self._reconnect_attempt = 0
        self._stopped = False
        self._state = "CONNECTING"
        self._result_cache: dict[str, dict] = {}
        self._ready_waiters: list[threading.Event] = []
        self._thread: Optional[threading.Thread] = None

    def get_state(self) -> str:
        return self._state

    def get_device_id(self) -> Optional[str]:
        return self._device_id

    def start(self) -> None:
        self._load_persisted_identity()
        self._load_persisted_result_cache()
        self._pair_if_needed()
        self._thread = threading.Thread(target=self._connection_loop, daemon=True)
        self._thread.start()

    def wait_until_ready(self, timeout_s: float = 15.0) -> None:
        if self._state == "READY":
            return
        event = threading.Event()
        self._ready_waiters.append(event)
        if not event.wait(timeout_s):
            raise TimeoutError("slash-agent READY 대기 타임아웃")

    def stop(self) -> None:
        self._stopped = True
        self._state = "STOPPED"
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass

    def _log(self, line: str) -> None:
        self._options.log(f"[slash-agent] {line}")

    # ---- 기기 식별 정보 영속화 ----

    def _load_persisted_identity(self) -> None:
        if self._device_token or not self._options.identity_store:
            return
        persisted = self._options.identity_store.load()
        if not persisted:
            return
        self._key_pair = restore_agent_key_pair(persisted.private_key_pem, persisted.public_key_base64)
        self._device_id = persisted.device_id
        self._device_token = persisted.device_token
        self._log(f"저장된 기기 ID를 불러왔습니다 deviceId={self._device_id}")

    def _persist_identity(self) -> None:
        if not self._options.identity_store or not self._device_id or not self._device_token:
            return
        self._options.identity_store.save(
            PersistedAgentIdentity(
                device_id=self._device_id,
                device_token=self._device_token,
                private_key_pem=self._key_pair.export_private_key_pem(),
                public_key_base64=self._key_pair.public_key_base64,
            )
        )

    # ---- 처리 이력(resultCache) 영속화 ----

    def _load_persisted_result_cache(self) -> None:
        if not self._options.processed_task_store:
            return
        records = self._options.processed_task_store.load()
        for key, value in records.items():
            self._result_cache[key] = value
        self._prune_result_cache()
        if self._result_cache:
            self._log(f"저장된 처리 이력 {len(self._result_cache)}건을 불러왔습니다")

    def _prune_result_cache(self) -> None:
        now = time.time()
        stale_keys = [
            key
            for key, cached in self._result_cache.items()
            if cached.get("acked") and now - _iso_to_epoch(cached["completed_at"]) > PROCESSED_TASK_RETENTION_S
        ]
        for key in stale_keys:
            del self._result_cache[key]

    def _persist_result_cache(self) -> None:
        if not self._options.processed_task_store:
            return
        self._prune_result_cache()
        self._options.processed_task_store.save(self._result_cache)

    # ---- 페어링/토큰 갱신 ----

    def _try_refresh_session(self) -> bool:
        """이미 등록된 기기라면 재페어링 대신 토큰 갱신을 시도한다(메시지 프로토콜 문서 §8.1 3단계)."""
        if not self._device_id or not self._device_token:
            return False
        try:
            refresh_nonce = str(uuid.uuid4())
            requested_at = now_iso_kst()
            signature = self._key_pair.sign(
                build_refresh_signing_payload(self._device_id, refresh_nonce, requested_at)
            )
            response = refresh_session(
                self._options.api_base_url, self._device_token, self._device_id, refresh_nonce, requested_at, signature
            )
            self._device_token = response["deviceToken"]
            self._log(f"기기 인증 토큰을 갱신했습니다 deviceId={self._device_id}")
            self._persist_identity()
            return True
        except Exception as e:
            self._log(f"토큰 갱신 실패, 재페어링으로 전환합니다: {e}")
            return False

    def _pair_if_needed(self) -> None:
        if self._device_token and self._device_id:
            if self._try_refresh_session():
                return
            self._device_id = None
            self._device_token = None
            if self._options.identity_store:
                self._options.identity_store.clear()

        if not self._options.pairing_code:
            raise RuntimeError("pairing_code 또는 preset_device_id/preset_device_token 중 하나는 반드시 필요합니다.")

        pair_response = pair_agent(
            self._options.api_base_url,
            self._options.pairing_code,
            self._key_pair.public_key_base64,
            self._options.device_name,
            list(SUPPORTED_TASK_TYPES),
        )
        signature = self._key_pair.sign(
            build_challenge_signing_payload(pair_response["challengeId"], pair_response["nonce"], pair_response["deviceId"])
        )
        verify_response = verify_pairing(
            self._options.api_base_url, pair_response["pairingSessionId"], pair_response["challengeId"], signature
        )
        self._device_id = pair_response["deviceId"]
        self._device_token = verify_response["deviceToken"]
        self._log(f"페어링 완료 deviceId={self._device_id}")
        self._persist_identity()

    # ---- 연결 루프 ----

    def _connection_loop(self) -> None:
        while not self._stopped:
            try:
                self._connect_once()
            except Exception as e:
                self._log(f"연결 오류: {e}")
            if self._stopped:
                return
            self._state = "OFFLINE"
            delay_s = min(30.0, 1.0 * 2**self._reconnect_attempt) + random.uniform(0, 0.25)
            self._reconnect_attempt += 1
            self._log(f"재연결 대기 {delay_s * 1000:.0f}ms")
            time.sleep(delay_s)

    def _connect_once(self) -> None:
        ws_url = self._options.api_base_url.replace("http", "ws", 1) + "/ws/agent"
        self._state = "CONNECTING"
        socket = ws_client.connect(ws_url, additional_headers={"Authorization": f"Bearer {self._device_token}"})
        self._socket = socket
        try:
            self._reconnect_attempt = 0
            self._state = "AUTHENTICATING"
            self._send(socket, "HELLO", **self._build_hello())

            last_heartbeat = time.monotonic()
            while not self._stopped:
                try:
                    raw = socket.recv(timeout=1.0)
                except TimeoutError:
                    raw = None
                except ConnectionClosed:
                    break

                if raw is not None:
                    self._handle_message(socket, raw)

                if self._state == "READY" and time.monotonic() - last_heartbeat > self._options.heartbeat_interval_s:
                    self._send_heartbeat(socket)
                    last_heartbeat = time.monotonic()
        finally:
            self._socket = None

    def _send(self, socket, message_type: str, **fields) -> None:
        socket.send(json.dumps(envelope(message_type, **fields)))

    def _build_hello(self) -> dict:
        return dict(
            deviceId=self._device_id,
            agentVersion="slash-agent-py/0.1.0",
            os="MACOS",
            architecture="ARM64" if platform.machine() in ("arm64", "aarch64") else "X86_64",
            osVersion=platform.platform(),
            supportedTaskTypes=list(SUPPORTED_TASK_TYPES),
        )

    def _build_ready(self) -> dict:
        return dict(
            maxConcurrentTasks=1,
            supportedTaskTypes=list(SUPPORTED_TASK_TYPES),
            searchFolders=[],
            projectWorkspaces=[],
        )

    def _send_heartbeat(self, socket) -> None:
        status = collect_system_status()
        self._send(
            socket,
            "HEARTBEAT",
            deviceId=self._device_id,
            cpuPercent=status["cpuPercent"],
            memoryPercent=status["memoryPercent"],
            runningTaskId=None,
        )

    def _fire_ready_waiters(self) -> None:
        waiters, self._ready_waiters = self._ready_waiters, []
        for event in waiters:
            event.set()

    def _resend_unacked_results(self, socket) -> None:
        for cached in self._result_cache.values():
            if not cached.get("acked"):
                self._send(socket, "RESULT", **cached["result"])

    def _handle_message(self, socket, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        msg_type = message.get("type")

        if msg_type == "CHALLENGE":
            signature = self._key_pair.sign(
                build_challenge_signing_payload(message["challengeId"], message["nonce"], self._device_id)
            )
            self._send(socket, "AUTH", challengeId=message["challengeId"], signature=signature)
            self._send(socket, "READY", **self._build_ready())
            self._state = "READY"
            self._log("READY 전송 완료")
            self._fire_ready_waiters()
            self._resend_unacked_results(socket)
            return

        if msg_type == "RESULT_ACK":
            key = f"{message['taskId']}:{message['dispatchId']}"
            cached = self._result_cache.get(key)
            if cached:
                cached["acked"] = True
                self._persist_result_cache()
            return

        if msg_type == "PROTOCOL_ERROR":
            self._log(f"PROTOCOL_ERROR 수신: {message.get('code')} {message.get('message')}")
            return

        if msg_type == "TASK":
            self._handle_task(socket, message)
            return

    # ---- TASK 처리 ----

    def _handle_task(self, socket, message: dict) -> None:
        key = f"{message['taskId']}:{message['dispatchId']}"
        cached = self._result_cache.get(key)
        if cached:
            self._log(f"중복 TASK 수신({key}) — 재실행 없이 기존 결과 재전송")
            self._send(socket, "ACK", **cached["ack"])
            self._send(socket, "RESULT", **cached["result"])
            return

        acknowledged_at = now_iso_kst()
        rejection = self._validate_task(message)
        ack_fields = dict(
            taskId=message["taskId"],
            dispatchId=message["dispatchId"],
            correlationId=message["correlationId"],
            accepted=rejection is None,
            reasonCode=rejection,
            acknowledgedAt=acknowledged_at,
        )
        self._send(socket, "ACK", **ack_fields)
        if rejection:
            self._log(f"TASK 거부({message.get('taskType')}): {rejection}")
            return

        started_at = now_iso_kst()
        self._send(
            socket,
            "PROGRESS",
            taskId=message["taskId"],
            dispatchId=message["dispatchId"],
            correlationId=message["correlationId"],
            stage="EXECUTING",
            percent=50,
        )

        outcome = self._execute_task(message)
        finished_at = now_iso_kst()
        result_fields = dict(
            taskId=message["taskId"],
            dispatchId=message["dispatchId"],
            correlationId=message["correlationId"],
            status="SUCCEEDED" if outcome["ok"] else "FAILED",
            result=outcome.get("result") if outcome["ok"] else None,
            error=None if outcome["ok"] else outcome["error"],
            startedAt=started_at,
            finishedAt=finished_at,
        )
        self._send(socket, "RESULT", **result_fields)

        self._result_cache[key] = {
            "ack": ack_fields,
            "result": result_fields,
            "acked": False,
            "completed_at": finished_at,
        }
        self._persist_result_cache()

    def _validate_task(self, message: dict) -> Optional[str]:
        if message["taskType"] not in SUPPORTED_TASK_TYPES:
            return "TASK_TYPE_NOT_SUPPORTED"
        if _iso_to_epoch(message["expiresAt"]) < time.time():
            return "TASK_EXPIRED"
        return None

    def _execute_task(self, message: dict) -> dict:
        try:
            if message["taskType"] == "SYSTEM_STATUS":
                return {"ok": True, "result": collect_system_status()}
            return {
                "ok": False,
                "error": {"code": "TASK_TYPE_NOT_SUPPORTED", "message": "unsupported task type", "retryable": False},
            }
        except Exception as e:
            return {"ok": False, "error": {"code": "POLICY_DENIED", "message": str(e), "retryable": False}}
