"""mock-api의 Agent 페어링·WSS 취급을 최소 재현한 시험 전용 서버 — fakeAgentServer.ts 대응.

목적: agent.py의 재연결·중복방지·토큰 갱신 흐름을 실제 네트워크·서명 검증으로 시험.
범위: 시험에 필요한 최소 라우트만, 실제 mock-api와 무관한 독립 구현.

REST(POST 바디 필요)와 WS를 같은 포트에서 서빙해야 해서(agent.py가 api_base_url의
http→ws 치환만으로 WSS 주소를 만들기 때문) aiohttp로 구현 — pytest는 동기 코드라
서버는 자체 이벤트루프를 백그라운드 스레드에서 돌리고, threading.Lock/Event로 넘나든다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket as socket_module
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from aiohttp import web
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from slash_pc_runner.protocol import (
    build_challenge_signing_payload,
    build_refresh_signing_payload,
    envelope,
    now_iso_kst,
)


def verify_signature(payload: str, signature_base64: str, public_key_base64: str) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_base64))
        public_key.verify(base64.b64decode(signature_base64), payload.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError):
        return False


def _find_free_port() -> int:
    with socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


class FakePcRunnerServer:
    def __init__(self):
        self.devices: dict[str, dict] = {}
        self.pairing_sessions: dict[str, dict] = {}
        self.ready_count = 0
        self.auto_ack_result = True
        # 지정 시 이 코드만 페어링 성공, 나머지는 PAIRING_CODE_INVALID(None = 전부 허용)
        self.accepted_pairing_code: Optional[str] = None

        self._lock = threading.Lock()
        self._received_messages: list[dict] = []
        self._waiters: list[tuple[str, threading.Event, dict]] = []
        self._current_ws: Optional[web.WebSocketResponse] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._runner: Optional[web.AppRunner] = None
        self.url = ""

    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        port = asyncio.run_coroutine_threadsafe(self._start_app(), self._loop).result(timeout=10)
        self.url = f"http://localhost:{port}"

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _start_app(self) -> int:
        app = web.Application()
        app.router.add_post("/api/v1/agent/pair", self._handle_pair)
        app.router.add_post("/api/v1/agent/pair/verify", self._handle_verify)
        app.router.add_post("/api/v1/agent/sessions/refresh", self._handle_refresh)
        app.router.add_get("/ws/agent", self._handle_ws)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        port = _find_free_port()
        site = web.TCPSite(self._runner, "localhost", port)
        await site.start()
        return port

    def close(self) -> None:
        async def _cleanup():
            await self._runner.cleanup()

        asyncio.run_coroutine_threadsafe(_cleanup(), self._loop).result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    # ---- REST: 페어링/갱신 ----

    @staticmethod
    def _ok(status: int, data: dict) -> web.Response:
        return web.json_response({"data": data}, status=status)

    @staticmethod
    def _fail(status: int, code: str, message: str) -> web.Response:
        return web.json_response({"error": {"code": code, "message": message}}, status=status)

    async def _handle_pair(self, request: web.Request) -> web.Response:
        body = await request.json()
        if self.accepted_pairing_code is not None and body.get("pairingCode") != self.accepted_pairing_code:
            return self._fail(422, "PAIRING_CODE_INVALID", "등록 코드가 올바르지 않습니다")

        device_id = str(uuid.uuid4())
        with self._lock:
            self.devices[device_id] = {
                "deviceId": device_id,
                "publicKeyBase64": body["publicKey"],
                "deviceToken": "",
                "revoked": False,
            }
        pairing_session_id = str(uuid.uuid4())
        challenge_id = str(uuid.uuid4())
        nonce = base64.b64encode(str(uuid.uuid4()).encode()).decode()
        with self._lock:
            self.pairing_sessions[pairing_session_id] = {
                "pairingSessionId": pairing_session_id,
                "deviceId": device_id,
                "challengeId": challenge_id,
                "nonce": nonce,
                "publicKeyBase64": body["publicKey"],
            }
        return self._ok(
            201,
            {
                "pairingSessionId": pairing_session_id,
                "deviceId": device_id,
                "challengeId": challenge_id,
                "nonce": nonce,
                "expiresAt": now_iso_kst(),
            },
        )

    async def _handle_verify(self, request: web.Request) -> web.Response:
        body = await request.json()
        session = self.pairing_sessions.get(body.get("pairingSessionId"))
        if not session or session["challengeId"] != body.get("challengeId"):
            return self._fail(404, "RESOURCE_NOT_FOUND", "세션 없음")
        valid = verify_signature(
            build_challenge_signing_payload(session["challengeId"], session["nonce"], session["deviceId"]),
            body.get("signature", ""),
            session["publicKeyBase64"],
        )
        if not valid:
            return self._fail(422, "AGENT_AUTH_FAILED", "서명 검증 실패")
        device = self.devices[session["deviceId"]]
        device["deviceToken"] = str(uuid.uuid4())
        return self._ok(200, {"deviceToken": device["deviceToken"], "expiresIn": 86400, "issuedAt": now_iso_kst(), "wsUrl": ""})

    async def _handle_refresh(self, request: web.Request) -> web.Response:
        body = await request.json()
        device = self.devices.get(body.get("deviceId"))
        if not device:
            return self._fail(401, "AUTH_REQUIRED", "미등록 기기")
        if device["revoked"]:
            return self._fail(409, "FORBIDDEN", "등록 해제된 기기")
        valid = verify_signature(
            build_refresh_signing_payload(body["deviceId"], body["refreshNonce"], body["requestedAt"]),
            body.get("signature", ""),
            device["publicKeyBase64"],
        )
        if not valid:
            return self._fail(403, "AGENT_AUTH_FAILED", "서명 검증 실패")
        device["deviceToken"] = str(uuid.uuid4())
        return self._ok(200, {"deviceToken": device["deviceToken"], "expiresIn": 86400, "issuedAt": now_iso_kst()})

    # ---- WS ----

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        with self._lock:
            self._current_ws = ws
        hello_device_id: Optional[str] = None
        challenge: Optional[dict] = None

        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                message = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            self._record_message(message)
            msg_type = message.get("type")

            if msg_type == "HELLO":
                hello_device_id = message["deviceId"]
                challenge_id = str(uuid.uuid4())
                nonce = base64.b64encode(str(uuid.uuid4()).encode()).decode()
                challenge = {"challengeId": challenge_id, "nonce": nonce}
                await ws.send_str(
                    json.dumps(envelope("CHALLENGE", challengeId=challenge_id, nonce=nonce, expiresAt=now_iso_kst()))
                )
                continue

            if msg_type == "AUTH":
                device = self.devices.get(hello_device_id) if hello_device_id else None
                valid = bool(
                    device
                    and challenge
                    and verify_signature(
                        build_challenge_signing_payload(challenge["challengeId"], challenge["nonce"], hello_device_id),
                        message.get("signature", ""),
                        device["publicKeyBase64"],
                    )
                )
                if not valid:
                    continue
                await ws.send_str(
                    json.dumps(
                        envelope(
                            "READY",
                            maxConcurrentTasks=1,
                            supportedTaskTypes=["FILE_SEARCH", "SYSTEM_STATUS"],
                            searchFolders=[],
                            projectWorkspaces=[],
                        )
                    )
                )
                with self._lock:
                    self.ready_count += 1
                continue

            if msg_type == "RESULT" and self.auto_ack_result:
                await ws.send_str(
                    json.dumps(
                        envelope(
                            "RESULT_ACK",
                            taskId=message["taskId"],
                            dispatchId=message["dispatchId"],
                            correlationId=message["correlationId"],
                            persisted=True,
                            taskStatus="SUCCEEDED",
                        )
                    )
                )

        with self._lock:
            if self._current_ws is ws:
                self._current_ws = None
        return ws

    def _record_message(self, message: dict) -> None:
        matched_event: Optional[threading.Event] = None
        with self._lock:
            self._received_messages.append(message)
            remaining = []
            for type_, event, box in self._waiters:
                if matched_event is None and type_ == message.get("type"):
                    box["message"] = message
                    matched_event = event
                else:
                    remaining.append((type_, event, box))
            self._waiters = remaining
        if matched_event is not None:
            matched_event.set()

    @property
    def received_messages(self) -> list[dict]:
        with self._lock:
            return list(self._received_messages)

    def wait_for_message(self, type_: str, timeout_s: float = 5.0, since_index: int = 0) -> dict:
        """sinceIndex 이후 기수신 메시지 즉시 반환, 없으면 신규 대기."""
        with self._lock:
            for message in self._received_messages[since_index:]:
                if message.get("type") == type_:
                    return message
            event = threading.Event()
            box: dict = {}
            self._waiters.append((type_, event, box))
        if not event.wait(timeout_s):
            raise TimeoutError(f"{type_} 대기 시간 초과")
        return box["message"]

    def send_task(self, task_id: str, dispatch_id: str, task_type: str, parameters: dict) -> None:
        """TASK 프레임 전송 — 마지막 READY 연결 대상."""
        if self._current_ws is None:
            raise RuntimeError("연결된 에이전트 소켓이 없습니다")
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
        message = envelope(
            "TASK",
            taskId=task_id,
            dispatchId=dispatch_id,
            correlationId=str(uuid.uuid4()),
            taskType=task_type,
            parameters=parameters,
            expiresAt=expires_at,
            payloadSha256="0" * 64,
        )

        async def _send():
            await self._current_ws.send_str(json.dumps(message))

        asyncio.run_coroutine_threadsafe(_send(), self._loop).result(timeout=5)

    def disconnect_agent(self) -> None:
        """소켓 강제 종료 — 재연결 시나리오 유발용."""
        ws = self._current_ws
        if ws is None:
            return

        async def _close():
            await ws.close()

        asyncio.run_coroutine_threadsafe(_close(), self._loop).result(timeout=5)


def start_fake_pc_runner_server() -> FakePcRunnerServer:
    server = FakePcRunnerServer()
    server.start()
    return server
