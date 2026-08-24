"""로컬 에이전트 핵심 — agent.ts(ContractPcRunner) 대응.

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
from pathlib import Path
from typing import Callable, Optional

import websockets.sync.client as ws_client
from websockets.exceptions import ConnectionClosed

from .code_adapters import (
    AVAILABILITY_CHECKS as CODE_ADAPTER_AVAILABILITY_CHECKS,
    RUNNERS as CODE_ADAPTER_RUNNERS,
    ProjectWorkspaceConfig,
)
from .summary_adapters import (
    AVAILABILITY_CHECKS as SUMMARY_ADAPTER_AVAILABILITY_CHECKS,
    RUNNERS as SUMMARY_ADAPTER_RUNNERS,
    SUMMARY_MAX_INPUT_CHARS,
)
from ._build_info import get_agent_version
from .crypto import AgentKeyPair, generate_agent_key_pair, restore_agent_key_pair
from .file_actions import reveal_in_file_manager
from .file_index import FileIndexStore, SearchFolderConfig
from .identity_store import AgentIdentityStore, PersistedAgentIdentity
from .pairing_client import DeviceRevokedError, pair_agent, refresh_session, verify_pairing
from .platform_info import detect_architecture, detect_os
from .processed_task_store import ProcessedTaskStore
from .protocol import (
    build_challenge_signing_payload,
    build_refresh_signing_payload,
    envelope,
    now_iso_kst,
)
from .system_status import collect_system_status
from .usage_adapters import COLLECTORS

SUPPORTED_TASK_TYPES: tuple[str, ...] = (
    "FILE_SEARCH",
    "FILE_OPEN",
    "SYSTEM_STATUS",
    "AI_AGENT_USAGE",
    "CODE_ANALYSIS",
    "TEXT_SUMMARY",
)

# RESULT_ACK 수신 후 재수신 대비 보관 기간
PROCESSED_TASK_RETENTION_S = 60 * 60

# 실행 중 진행률 보고 주기 — CODE_ANALYSIS처럼 오래 걸리는 작업(최대 300초)이 시작 시점의
# PROGRESS(0%) 하나만 보내고 끝까지 조용하면 화면에서 멈춘 것처럼 보인다.
PROGRESS_TICK_INTERVAL_S = 15.0

# slash-api의 tasks.result 컬럼에 CHECK(octet_length(result::text) <= 65536) 제약이 걸려
# 있다(V004/V006 마이그레이션 확인) — CODE_ANALYSIS·TEXT_SUMMARY 둘 다 summary 필드에 CLI
# 출력을 그대로 담으므로 길이 제한이 없으면 이 상한을 넘겨 서버 저장 단계에서 실패할 수
# 있다. 다른 필드·JSON 구조 자체의 오버헤드, summary 안의 JSON 이스케이프 확장분을 감안해
# 여유를 두고 자른다. (TEXT_SUMMARY는 3문장 이내로 짧게 끝나도록 프롬프트로 유도하지만,
# 프롬프트를 CLI가 항상 따른다는 보장은 없어 방어적으로 CODE_ANALYSIS와 같은 안전장치를 쓴다.)
RESULT_JSON_BYTE_LIMIT = 65536
RESULT_TRUNCATION_MARKER = "\n\n...(결과가 너무 길어 일부가 잘렸습니다)"


def _iso_to_epoch(iso_str: str) -> float:
    return datetime.fromisoformat(iso_str).timestamp()


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """UTF-8 인코딩 기준 max_bytes 이하로 자른다. 멀티바이트 문자 중간에서 잘리지 않도록
    유효한 UTF-8이 될 때까지 뒤에서부터 줄인다."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def _truncate_summary_field(result: dict) -> dict:
    """result.summary가 서버 저장 상한을 넘으면 잘라 truncated 표시를 남긴다.
    CODE_ANALYSIS·TEXT_SUMMARY 둘 다 결과를 summary 필드에 담아 이 함수 하나를 같이 쓴다."""
    summary = result.get("summary")
    if not isinstance(summary, str):
        return result

    def encoded_size(candidate: dict) -> int:
        return len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))

    if encoded_size(result) <= RESULT_JSON_BYTE_LIMIT:
        return result

    # summary만 줄여가며 전체 JSON 크기가 상한 이하가 될 때까지 반복한다 — JSON 이스케이프로
    # 실제 바이트 수가 예상보다 늘어날 수 있어(따옴표·개행 등) 한 번에 계산하지 않고 검증한다.
    budget = len(summary.encode("utf-8"))
    while True:
        budget = int(budget * 0.9)
        candidate_summary = _truncate_utf8(summary, budget) + RESULT_TRUNCATION_MARKER
        candidate = {**result, "summary": candidate_summary, "truncated": True}
        if budget <= 0 or encoded_size(candidate) <= RESULT_JSON_BYTE_LIMIT:
            return candidate


@dataclass
class ContractPcRunnerOptions:
    api_base_url: str
    # 정상 페어링 경로. preset_device_id/preset_device_token을 주면 이 값은 무시되고 HTTP 페어링을 건너뛴다.
    pairing_code: Optional[str] = None
    device_name: str = "slash-pc-runner-py"
    heartbeat_interval_s: float = 30.0
    log: Callable[[str], None] = field(default=lambda line: None)
    # 시험 전용: 이미 발급된 deviceId/deviceToken을 직접 주입해 HTTP 페어링 단계를 생략한다.
    preset_device_id: Optional[str] = None
    preset_device_token: Optional[str] = None
    identity_store: Optional[AgentIdentityStore] = None
    processed_task_store: Optional[ProcessedTaskStore] = None
    # 검색 대상 폴더 목록(정적 설정) — 등록 UI는 아직 없어서 지금은 시작 시 고정 목록만 지원
    search_folders: list = field(default_factory=list)
    file_index_store: Optional[FileIndexStore] = None
    # CODE_ANALYSIS 대상 프로젝트 폴더 목록(정적 설정) — search_folders와 같은 이유로 등록 UI 없음
    project_workspaces: list[ProjectWorkspaceConfig] = field(default_factory=list)


class ContractPcRunner:
    def __init__(self, options: ContractPcRunnerOptions):
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
        # CODE_ANALYSIS 같은 긴 작업은 _execute_task가 연결 루프를 동기로 몇십 초~5분씩 막는다
        # — 그동안 HEARTBEAT도 못 나가는데, api 쪽에 하트비트 끊긴 기기를 OFFLINE으로 내리는
        # 배치(markOfflineWhenHeartbeatStale)가 나중에 켜지면 작업 중인 기기를 죽은 걸로
        # 오판할 수 있다. 그래서 하트비트는 별도 스레드로 돌려 작업 실행과 무관하게 계속 나가게
        # 한다. 소켓 하나를 두 스레드(이 스레드 + 연결 루프)가 같이 쓰므로 전송에 락이 필요하다.
        self._send_lock = threading.Lock()
        self._heartbeat_stop: Optional[threading.Event] = None
        # 작업은 한 번에 하나만 처리한다(READY의 maxConcurrentTasks=1과 일치) — 서버가 기기당
        # 활성 전달을 하나로 제한해서 정상 흐름에서는 겹칠 일이 없지만, 방어적으로 락을 건다.
        self._task_lock = threading.Lock()
        self._running_task_id: Optional[str] = None

    def get_state(self) -> str:
        return self._state

    def get_device_id(self) -> Optional[str]:
        return self._device_id

    def start(self) -> None:
        # 폴더 색인은 기다리지 않는다 — 큰 폴더 때문에 WSS 연결·READY가 늦어지면 안 된다.
        # 스캔이 끝나기 전엔 _build_ready()가 INDEXING 상태를 그대로 보고한다.
        if self._options.file_index_store is not None:
            threading.Thread(
                target=self._options.file_index_store.sync_folders,
                args=(self._options.search_folders,),
                daemon=True,
            ).start()
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
            raise TimeoutError("slash-pc-runner READY 대기 타임아웃")

    def stop(self) -> None:
        self._stopped = True
        self._state = "STOPPED"
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass

    def _log(self, line: str) -> None:
        self._options.log(f"[slash-pc-runner] {line}")

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
        except DeviceRevokedError as e:
            # 권한 문제와 달리 재페어링으로 회복되지 않는다 — 그대로 두면 호출부가
            # pairing_code 유무에 따라 새로 페어링을 시도하거나 죽는데, 둘 다 틀렸다.
            # 여기서 식별 정보를 정리하고 그대로 전파해 호출부가 재페어링을 아예
            # 시도하지 않게 한다(WSS 경로의 PROTOCOL_ERROR DEVICE_REVOKED 처리와 동일 원칙).
            self._log(f"기기 등록이 해제되었습니다: {e}")
            if self._options.identity_store:
                self._options.identity_store.clear()
            raise
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
        self._heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, args=(socket, self._heartbeat_stop), daemon=True
        )
        heartbeat_thread.start()
        try:
            self._state = "AUTHENTICATING"
            self._send(socket, "HELLO", **self._build_hello())

            while not self._stopped:
                try:
                    raw = socket.recv(timeout=1.0)
                except TimeoutError:
                    raw = None
                except ConnectionClosed:
                    break

                if raw is not None:
                    self._handle_message(socket, raw)
        finally:
            self._heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)
            self._socket = None

    def _heartbeat_loop(self, socket, stop_event: threading.Event) -> None:
        # 연결 루프와 별도 스레드로 돈다 — CODE_ANALYSIS처럼 몇십 초~5분 걸리는 작업이
        # 백그라운드 스레드에서 도는 동안에도(아래 _handle_message의 TASK 분기 참고)
        # 하트비트는 끊기지 않고 계속 나가야, api 쪽 하트비트 만료 판정이 실행 중인 기기를
        # 죽은 걸로 오판하지 않는다.
        while not stop_event.wait(self._options.heartbeat_interval_s):
            if self._state != "READY":
                continue
            try:
                self._send_heartbeat(socket)
            except Exception as e:
                self._log(f"하트비트 전송 실패: {e}")
                return

    def _send(self, socket, message_type: str, **fields) -> None:
        with self._send_lock:
            socket.send(json.dumps(envelope(message_type, **fields)))

    def _build_hello(self) -> dict:
        return dict(
            deviceId=self._device_id,
            agentVersion=get_agent_version(),
            os=detect_os(),
            architecture=detect_architecture(),
            osVersion=platform.platform(),
            supportedTaskTypes=list(SUPPORTED_TASK_TYPES),
        )

    def _build_ready(self) -> dict:
        search_folders = self._options.file_index_store.list_search_folders() if self._options.file_index_store else []
        project_workspaces = [
            dict(
                workspaceId=workspace.workspace_id,
                displayName=workspace.display_name,
                workspaceType=workspace.workspace_type,
                availableCodeAdapters=list(workspace.available_code_adapters),
            )
            for workspace in self._options.project_workspaces
        ]
        # slash-api는 아직 TEXT_SUMMARY를 LLM_SERVICE로만 라우팅해 이 값을 안 쓴다(RUN-01) —
        # CODE_ANALYSIS의 projectWorkspaces가 그랬듯, 라우팅이 붙기 전에 능력치부터 미리
        # 보고해 둔다.
        available_summary_adapters = [
            name for name, check in SUMMARY_ADAPTER_AVAILABILITY_CHECKS.items() if check()
        ]
        return dict(
            maxConcurrentTasks=1,
            supportedTaskTypes=list(SUPPORTED_TASK_TYPES),
            searchFolders=search_folders,
            projectWorkspaces=project_workspaces,
            availableSummaryAdapters=available_summary_adapters,
        )

    def _send_heartbeat(self, socket) -> None:
        status = collect_system_status()
        self._send(
            socket,
            "HEARTBEAT",
            deviceId=self._device_id,
            cpuPercent=status["cpuPercent"],
            memoryPercent=status["memoryPercent"],
            runningTaskId=self._running_task_id,
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
            # 소켓 연결(101) 직후가 아니라 여기서 리셋해야 한다 — 서버가 인증 단계에서 매번
            # 거부해도(예: 해제된 기기) 백오프가 항상 1초로 되돌아가지 않고 실제로 늘어난다.
            self._reconnect_attempt = 0
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
            code = message.get("code")
            self._log(f"PROTOCOL_ERROR 수신: {code} {message.get('message')}")
            if code == "DEVICE_REVOKED":
                # 저장된 식별 정보를 지워도 지금 돌고 있는 이 프로세스의 self._device_token은
                # 메모리에 그대로 남는다 — stop()으로 재연결 루프 자체를 멈춰야 같은 죽은
                # 토큰으로 계속 재시도하는 걸 막을 수 있다.
                if self._options.identity_store:
                    self._options.identity_store.clear()
                self.stop()
            return

        if msg_type == "TASK":
            # 여기서 바로 처리하면 CODE_ANALYSIS 같은 긴 작업이 연결 루프 자체를 막아서, 그동안
            # 다른 메시지도 못 받고(다만 서버가 기기당 활성 전달을 하나로 제한해 정상 흐름에서
            # 겹칠 일은 없다) 무엇보다 하트비트 스레드 쪽 상태 반영이 늦어진다 — 별도 스레드로
            # 넘겨서 연결 루프는 계속 수신 대기 상태를 유지하게 한다. _task_lock으로 직렬화하므로
            # maxConcurrentTasks=1은 그대로 지켜진다.
            threading.Thread(target=self._run_task, args=(socket, message), daemon=True).start()
            return

    def _run_task(self, socket, message: dict) -> None:
        # 백그라운드 스레드라 연결이 끊긴 뒤에도 이 스레드는 옛 소켓 참조를 들고 있을 수 있다
        # — 그 사이 연결 루프가 먼저 끊김을 감지해 재연결로 넘어가면 여기서 보내는 ACK/RESULT는
        # 닫힌 소켓에 쓰는 꼴이 된다. 처리 자체(캐시 반영 등)는 이미 끝났을 수 있고, 못 보낸
        # 메시지는 재연결 후 _resend_unacked_results가 다시 보내므로 여기서는 조용히 넘어간다.
        try:
            with self._task_lock:
                self._handle_task(socket, message)
        except ConnectionClosed as e:
            self._log(f"작업 응답 전송 중 연결 끊김(재연결 후 재전송됨): {e}")

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
            percent=0,
            **self._progress_message_for(message["taskType"]),
        )

        self._running_task_id = message["taskId"]
        progress_stop = threading.Event()
        progress_thread = threading.Thread(
            target=self._progress_ticker, args=(socket, message, progress_stop), daemon=True
        )
        progress_thread.start()
        try:
            outcome = self._execute_task(socket, message)
        finally:
            progress_stop.set()
            progress_thread.join(timeout=2.0)
            self._running_task_id = None
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
        # 캐시에 먼저 남기고 나서 보낸다 — 순서를 반대로 하면 전송이 끊긴 연결 때문에 실패할 때
        # (아래 _run_task가 ConnectionClosed를 잡아 조용히 넘어가는 경우) 결과가 캐시에 없어서
        # 재연결 후 _resend_unacked_results가 재전송할 대상 자체가 없어진다 — 작업이 유실된
        # 것처럼 보인다. 먼저 캐시해두면 전송이 몇 번을 실패해도 재연결 시 반드시 다시 나간다.
        self._result_cache[key] = {
            "ack": ack_fields,
            "result": result_fields,
            "acked": False,
            "completed_at": finished_at,
        }
        self._persist_result_cache()
        self._send(socket, "RESULT", **result_fields)

    def _progress_ticker(self, socket, message: dict, stop_event: threading.Event) -> None:
        # 정확한 진행률은 계산할 방법이 없다(LLM 호출이 얼마나 남았는지 알 수 없음) — 그래도
        # 완료될 때까지 주기적으로 신호를 보내 "멈춘 게 아니라 계속 일하고 있다"는 것만이라도
        # 알린다. 100은 실제 완료 시 RESULT로만 도달하게 90에서 멈춘다.
        percent = 0
        while not stop_event.wait(PROGRESS_TICK_INTERVAL_S):
            percent = min(90, percent + 10)
            try:
                self._send(
                    socket,
                    "PROGRESS",
                    taskId=message["taskId"],
                    dispatchId=message["dispatchId"],
                    correlationId=message["correlationId"],
                    stage="EXECUTING",
                    percent=percent,
                    **self._progress_message_for(message["taskType"]),
                )
            except ConnectionClosed:
                return

    def _progress_message_for(self, task_type: str) -> dict:
        # CODE_ANALYSIS만 최대 300초까지 걸릴 수 있어 안내 문구가 필요하다 — 다른 TaskType은
        # 대부분 몇 초 안에 끝나 문구를 붙일 이유가 없다. message는 PROGRESS의 선택 필드라
        # (README 메시지 프로토콜 §2 참고) 해당 없으면 아예 안 보낸다.
        if task_type != "CODE_ANALYSIS":
            return {}
        return {"message": "코드를 분석하고 있어요. 최대 5분까지 걸릴 수 있어요."}

    def _send_turn_progress(self, socket, message: dict, turn: int) -> None:
        # CODEX 어댑터는 item.completed 이벤트로 턴 완료를 실시간으로 알 수 있어, 15초
        # 타이머보다 실제 진행 상황에 가까운 신호를 추가로 보낸다. 총 턴 수는 여전히 알 수
        # 없어 근사치인 건 같다 — _progress_ticker와 같은 0 기준·10단위·90 상한 규칙을 맞춘다.
        percent = min(90, turn * 10)
        try:
            self._send(
                socket,
                "PROGRESS",
                taskId=message["taskId"],
                dispatchId=message["dispatchId"],
                correlationId=message["correlationId"],
                stage="EXECUTING",
                percent=percent,
                message=f"{turn}번째 응답을 처리했어요.",
            )
        except ConnectionClosed:
            pass

    def _validate_task(self, message: dict) -> Optional[str]:
        if message["taskType"] not in SUPPORTED_TASK_TYPES:
            return "TASK_TYPE_NOT_SUPPORTED"
        if _iso_to_epoch(message["expiresAt"]) < time.time():
            return "TASK_EXPIRED"
        if message["taskType"] == "FILE_SEARCH":
            query = message["parameters"].get("query")
            search_folder_id = message["parameters"].get("searchFolderId")
            if not isinstance(query, str) or len(query) == 0:
                return "INVALID_PARAMETERS"
            store = self._options.file_index_store
            if not isinstance(search_folder_id, str) or store is None or not store.is_searchable(search_folder_id):
                return "SEARCH_FOLDER_NOT_FOUND"
        if message["taskType"] == "FILE_OPEN":
            file_ref = message["parameters"].get("fileRef")
            store = self._options.file_index_store
            if not isinstance(file_ref, str) or len(file_ref) == 0:
                return "INVALID_PARAMETERS"
            if store is None or store.resolve_file_ref(file_ref) is None:
                return "FILE_NOT_FOUND"
        if message["taskType"] == "AI_AGENT_USAGE":
            provider = message["parameters"].get("provider")
            if provider not in COLLECTORS:
                return "INVALID_PARAMETERS"
            if COLLECTORS[provider]() is None:
                return "CODE_AGENT_NOT_CONFIGURED"
        if message["taskType"] == "CODE_ANALYSIS":
            workspace = self._find_project_workspace(message["parameters"].get("workspaceId"))
            if workspace is None:
                return "WORKSPACE_NOT_FOUND"
            code_adapter = self._resolve_code_adapter(workspace, message["parameters"].get("codeAdapter"))
            if code_adapter is None:
                return "CODE_AGENT_NOT_CONFIGURED"
        if message["taskType"] == "TEXT_SUMMARY":
            text = message["parameters"].get("text")
            if not isinstance(text, str) or not text.strip():
                return "INVALID_PARAMETERS"
            # 상한 초과를 여기서 먼저 걸러야 INVALID_PARAMETERS로 나간다 — 이 검사가 없으면
            # summary_adapters._validate_input_length()가 실행 단계에서야 걸려
            # _execute_task의 공용 except가 POLICY_DENIED로 잘못 분류한다(입력값 문제인데
            # 권한/정책 문제로 보임, 실측 확인된 오분류).
            if len(text) > SUMMARY_MAX_INPUT_CHARS:
                return "INVALID_PARAMETERS"
            if self._resolve_summary_adapter() is None:
                # 별도 reasonCode를 새로 만들지 않는다 — "로컬 AI 도구가 설정되어 있지 않음"은
                # CODE_ANALYSIS와 의미가 같고, 이 값은 README에 문서화된 slash-api 소유
                # 프로토콜 계약이라 이 저장소가 임의로 값을 추가할 수 없다.
                return "CODE_AGENT_NOT_CONFIGURED"
        return None

    def _find_project_workspace(self, workspace_id: Optional[str]) -> Optional[ProjectWorkspaceConfig]:
        return next((w for w in self._options.project_workspaces if w.workspace_id == workspace_id), None)

    def _resolve_code_adapter(self, workspace: ProjectWorkspaceConfig, requested: Optional[str]) -> Optional[str]:
        # 요청에 codeAdapter가 없으면 그 워크스페이스가 지원하는 첫 번째 어댑터를 기본으로 쓴다.
        candidate = requested or (workspace.available_code_adapters[0] if workspace.available_code_adapters else None)
        if candidate is None or candidate not in workspace.available_code_adapters:
            return None
        check = CODE_ADAPTER_AVAILABILITY_CHECKS.get(candidate)
        if check is None or not check():
            return None
        return candidate

    def _resolve_summary_adapter(self) -> Optional[str]:
        # CODE_ANALYSIS와 달리 프로젝트 워크스페이스 단위가 아니라 PC 전체 기준이다 — 파일
        # 접근이 필요 없는 작업이라 "어느 폴더에서 쓸 수 있는지" 개념 자체가 없다. 계약에
        # 클라이언트가 어댑터를 지정하는 파라미터가 아직 없어 자동 선택만 지원한다.
        for name, check in SUMMARY_ADAPTER_AVAILABILITY_CHECKS.items():
            if check():
                return name
        return None

    def _execute_task(self, socket, message: dict) -> dict:
        try:
            if message["taskType"] == "SYSTEM_STATUS":
                return {"ok": True, "result": collect_system_status()}
            if message["taskType"] == "FILE_SEARCH":
                query = str(message["parameters"].get("query", ""))
                search_folder_id = str(message["parameters"].get("searchFolderId", ""))
                return {"ok": True, "result": self._options.file_index_store.search(search_folder_id, query)}
            if message["taskType"] == "FILE_OPEN":
                file_ref = str(message["parameters"]["fileRef"])
                path = self._options.file_index_store.resolve_file_ref(file_ref)
                if path is None:
                    # ACK 시점엔 있었지만 그 사이 파일이 삭제되는 등 경합(TOCTOU) 가능성이 있어
                    # 실행 시점에도 다시 확인한다.
                    return {
                        "ok": False,
                        "error": {"code": "FILE_NOT_FOUND", "message": "file reference not found", "retryable": False},
                    }
                reveal_in_file_manager(path)
                return {"ok": True, "result": {"revealedAt": now_iso_kst()}}
            if message["taskType"] == "AI_AGENT_USAGE":
                provider = message["parameters"]["provider"]
                return {"ok": True, "result": COLLECTORS[provider]()}
            if message["taskType"] == "CODE_ANALYSIS":
                workspace = self._find_project_workspace(message["parameters"].get("workspaceId"))
                code_adapter = self._resolve_code_adapter(workspace, message["parameters"].get("codeAdapter"))
                query = str(message["parameters"].get("query", ""))
                extra_kwargs = {}
                if code_adapter == "CODEX":
                    extra_kwargs["on_turn_complete"] = lambda turn: self._send_turn_progress(socket, message, turn)
                result = CODE_ADAPTER_RUNNERS[code_adapter](Path(workspace.root_path), query, **extra_kwargs)
                return {"ok": True, "result": _truncate_summary_field(result)}
            if message["taskType"] == "TEXT_SUMMARY":
                text = str(message["parameters"].get("text", ""))
                summary_adapter = self._resolve_summary_adapter()
                result = SUMMARY_ADAPTER_RUNNERS[summary_adapter](text)
                return {"ok": True, "result": _truncate_summary_field(result)}
            return {
                "ok": False,
                "error": {"code": "TASK_TYPE_NOT_SUPPORTED", "message": "unsupported task type", "retryable": False},
            }
        except Exception as e:
            return {"ok": False, "error": {"code": "POLICY_DENIED", "message": str(e), "retryable": False}}
