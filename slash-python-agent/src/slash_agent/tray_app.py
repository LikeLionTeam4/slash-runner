"""macOS 메뉴바 트레이 앱 — main.cjs(agent-app)의 트레이 부분 대응.

색인 폴더 관리 창은 별도 프로세스(folders_window.py)로 띄우고, search-folders.json 파일
변경을 여기서 주기적으로 감지해 실행 중인 에이전트에 반영한다(둘 다 자세한 이유는
folders_window.py 상단 주석 참고).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

import rumps

from .agent import ContractAgent, ContractAgentOptions
from .file_index import FileIndexStore, SearchFolderConfig
from .identity_store import KeyringIdentityStore
from .processed_task_store import JsonFileProcessedTaskStore
from .resources import is_frozen, repo_fixtures_search_folder, resource_path

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "slash-agent-py"
CONFIG_PATH = CONFIG_DIR / "config.json"
CONFIG_EXAMPLE_PATH = CONFIG_DIR / "config.example.json"
SEARCH_FOLDERS_PATH = CONFIG_DIR / "search-folders.json"
FILE_INDEX_DB_PATH = CONFIG_DIR / "file-index.sqlite3"
PROCESSED_TASKS_PATH = CONFIG_DIR / "processed-tasks.json"

STATE_LABEL = {
    "CONNECTING": "연결 중...",
    "AUTHENTICATING": "인증 중...",
    "READY": "READY",
    "OFFLINE": "오프라인 (재연결 시도 중)",
    "STOPPED": "중지됨",
}


def _default_search_folders() -> list[dict]:
    return [
        {
            "searchFolderId": "sf-fixtures-01",
            "displayName": "테스트 검색 폴더",
            "rootPath": str(repo_fixtures_search_folder()),
        }
    ]


def _load_search_folders() -> list[dict]:
    if not SEARCH_FOLDERS_PATH.exists():
        return _default_search_folders()
    try:
        return json.loads(SEARCH_FOLDERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default_search_folders()


def _load_config() -> dict:
    file_config: dict = {}
    if CONFIG_PATH.exists():
        try:
            file_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "apiBaseUrl": file_config.get("apiBaseUrl") or os.environ.get("SLASH_AGENT_API_BASE_URL", "http://localhost:4000"),
        "pairingCode": file_config.get("pairingCode") or os.environ.get("SLASH_AGENT_PAIRING_CODE"),
        "deviceName": file_config.get("deviceName") or os.environ.get("SLASH_AGENT_DEVICE_NAME", "slash-agent-py (macOS)"),
        "heartbeatIntervalS": float(
            file_config.get("heartbeatIntervalS") or os.environ.get("SLASH_AGENT_HEARTBEAT_INTERVAL_S", 30)
        ),
    }


def _log(line: str) -> None:
    # 파일/파이프로 리다이렉트되면 표준출력이 완전 버퍼링돼 프로세스 종료 전까지 안 보인다
    # (cli.py에서 이미 겪은 문제와 동일) — 매번 강제로 흘려보낸다.
    print(line, flush=True)


def _obtain_pairing_code(api_base_url: str) -> str:
    """등록 코드가 없으면 시험 전용 자동 로그인+등록코드 발급으로 채운다(cli.py와 동일한 편의 로직)."""
    login_req = urllib.request.Request(
        f"{api_base_url}/test/login",
        data=json.dumps({"email": "agent-py-app-tester@example.com", "displayName": "agent-py-app tester"}).encode(),
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
        return json.loads(res.read().decode())["data"]["pairingCode"]


class TrayApp(rumps.App):
    def __init__(self):
        icon_path = resource_path("assets", "trayIcon.png")
        super().__init__("Slash Agent", icon=str(icon_path) if icon_path.exists() else None, quit_button=None)

        self.agent: Optional[ContractAgent] = None
        self.file_index_store: Optional[FileIndexStore] = None
        self.current_config: dict = {}
        self._search_folders_mtime: Optional[float] = None

        self.status_item = rumps.MenuItem("상태: 연결 중...")
        self.device_item = rumps.MenuItem("기기 ID: -")
        self.api_item = rumps.MenuItem("mock-api: -")
        self.menu = [
            self.status_item,
            self.device_item,
            self.api_item,
            None,
            rumps.MenuItem("색인 폴더 관리", callback=self.open_folders_window),
            rumps.MenuItem("설정 폴더 열기", callback=self.open_config_folder),
            None,
            rumps.MenuItem("종료", callback=self.quit_app),
        ]

    def start_agent(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_EXAMPLE_PATH.exists():
            CONFIG_EXAMPLE_PATH.write_text(
                json.dumps(
                    {"apiBaseUrl": "http://localhost:4000", "pairingCode": "123456", "deviceName": "내 Mac", "heartbeatIntervalS": 30},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        self.current_config = _load_config()
        identity_store = KeyringIdentityStore(service_name="slash-agent-py-app")
        processed_task_store = JsonFileProcessedTaskStore(PROCESSED_TASKS_PATH)
        self.file_index_store = FileIndexStore(str(FILE_INDEX_DB_PATH))

        folders = _load_search_folders()
        self._search_folders_mtime = SEARCH_FOLDERS_PATH.stat().st_mtime if SEARCH_FOLDERS_PATH.exists() else None
        search_folders = [SearchFolderConfig(f["searchFolderId"], f["displayName"], f["rootPath"]) for f in folders]

        api_base_url = self.current_config["apiBaseUrl"]
        has_persisted_identity = identity_store.load() is not None
        pairing_code = self.current_config["pairingCode"] or (
            None if has_persisted_identity else _obtain_pairing_code(api_base_url)
        )

        def build_agent(code: Optional[str]) -> ContractAgent:
            return ContractAgent(
                ContractAgentOptions(
                    api_base_url=api_base_url,
                    pairing_code=code,
                    device_name=self.current_config["deviceName"],
                    heartbeat_interval_s=self.current_config["heartbeatIntervalS"],
                    log=_log,
                    identity_store=identity_store,
                    processed_task_store=processed_task_store,
                    search_folders=search_folders,
                    file_index_store=self.file_index_store,
                )
            )

        self.agent = build_agent(pairing_code)
        try:
            self.agent.start()
        except Exception:
            # 저장된 식별 정보로 토큰 갱신까지 실패했는데 pairingCode도 없었던 경우(드묾) —
            # 새 등록 코드를 받아 한 번만 새로 페어링을 시도한다.
            if pairing_code or not has_persisted_identity:
                raise
            self.agent = build_agent(_obtain_pairing_code(api_base_url))
            self.agent.start()

    @rumps.timer(2)
    def refresh(self, _sender=None) -> None:
        # 색인 폴더 관리 창은 같은 실행 파일을 인자만 바꿔 재사용한다(패키징 여부와 무관 —
        # __main__.py 주석 참고) — 그쪽 창이 떠 있는 동안 이 프로세스의 Dock 아이콘이 macOS
        # 쪽 사정으로 다시 나타나는 경우가 있어, 주기적으로 다시 눌러서 되돌린다.
        _hide_dock_icon()

        if self.agent is not None:
            state = self.agent.get_state()
            device_id = self.agent.get_device_id()
            self.status_item.title = f"상태: {STATE_LABEL.get(state, state)}"
            self.device_item.title = f"기기 ID: {(device_id[:8] + '…') if device_id else '-'}"
            self.api_item.title = f"mock-api: {self.current_config.get('apiBaseUrl', '-')}"

        # folders_window(별도 프로세스)가 search-folders.json을 바꿨는지 주기적으로 확인
        if self.file_index_store is not None and SEARCH_FOLDERS_PATH.exists():
            mtime = SEARCH_FOLDERS_PATH.stat().st_mtime
            if mtime != self._search_folders_mtime:
                self._search_folders_mtime = mtime
                folders = _load_search_folders()
                search_folders = [SearchFolderConfig(f["searchFolderId"], f["displayName"], f["rootPath"]) for f in folders]
                self.file_index_store.sync_folders(search_folders)

    def open_folders_window(self, _sender) -> None:
        if not SEARCH_FOLDERS_PATH.exists():
            SEARCH_FOLDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SEARCH_FOLDERS_PATH.write_text(json.dumps(_load_search_folders(), ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.Popen(_folders_window_command())

    def open_config_folder(self, _sender) -> None:
        subprocess.run(["open", "-R", str(CONFIG_PATH)])

    def quit_app(self, _sender) -> None:
        if self.agent is not None:
            self.agent.stop()
        if self.file_index_store is not None:
            self.file_index_store.close()
        rumps.quit_application()


def _folders_window_command() -> list[str]:
    # 얼린 실행 파일은 진입점이 하나뿐이라(sys.executable이 파이썬 인터프리터가 아니라
    # 이 앱 자신이다), "--folders-window" 인자로 같은 실행 파일을 다시 불러 __main__.py가
    # 분기하게 한다. 개발 모드는 지금처럼 -m으로 모듈을 직접 지정한다.
    if is_frozen():
        return [sys.executable, "--folders-window", str(SEARCH_FOLDERS_PATH)]
    return [sys.executable, "-m", "slash_agent.folders_window", str(SEARCH_FOLDERS_PATH)]


def _hide_dock_icon() -> None:
    # 패키징 전(Info.plist 없는 개발 모드)엔 macOS가 기본값으로 이 프로세스를 "Python"이라는
    # 이름과 기본 파이썬 아이콘으로 Dock에 띄운다 — 메뉴바 전용 앱이라 감춘다
    # (Electron agent-app의 app.dock.hide()와 동일한 목적).
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass


def main() -> None:
    app = TrayApp()
    try:
        app.start_agent()
    except Exception as e:
        print(f"에이전트 시작 실패: {e}", file=sys.stderr)
        app.status_item.title = f"상태: 시작 실패 - {e}"
    # rumps.App.run()이 내부에서 NSApplication.sharedApplication()을 다시 건드리므로
    # (activateIgnoringOtherApps_ 등), 그보다 먼저 정책을 바꿔봐야 소용없다 — run() 진입 직전에
    # 걸어야 한다. folders_window.py가 pywebview에서 겪은 것과 같은 종류의 순서 문제.
    _hide_dock_icon()
    app.run()


if __name__ == "__main__":
    main()
