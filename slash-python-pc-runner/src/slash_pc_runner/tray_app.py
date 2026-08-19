"""메뉴바/시스템 트레이 앱 — main.cjs(agent-app)의 트레이 부분 대응.

`pystray`로 macOS·Windows 공통 코드를 쓴다(원래 macOS 전용 `rumps`였으나, 같은 UI를
플랫폼별로 따로 만들지 않기 위해 통일했다 — 런타임 메모리는 실측 결과 rumps와 큰 차이가
없었고, PyInstaller 패키징 관점에서도 무거운 GUI 프레임워크(Qt 등)를 새로 끌어들이지
않는다).

색인 폴더 관리 창은 별도 프로세스(folders_window.py)로 띄우고, search-folders.json 파일
변경을 여기서 주기적으로 감지해 실행 중인 에이전트에 반영한다(둘 다 자세한 이유는
folders_window.py 상단 주석 참고).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

import pystray
from PIL import Image

from . import single_instance
from ._build_info import PACKAGE_VERSION, get_build_date, get_build_sha
from .agent import ContractPcRunner, ContractPcRunnerOptions
from .file_index import FileIndexStore, SearchFolderConfig
from .identity_store import KeyringIdentityStore
from .pairing_client import DeviceRevokedError
from .processed_task_store import JsonFileProcessedTaskStore
from .resources import config_dir, is_frozen, repo_fixtures_search_folder, resource_path
from .update_check import check_for_update

CONFIG_DIR = config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"
CONFIG_EXAMPLE_PATH = CONFIG_DIR / "config.example.json"
SEARCH_FOLDERS_PATH = CONFIG_DIR / "search-folders.json"
FILE_INDEX_DB_PATH = CONFIG_DIR / "file-index.sqlite3"
PROCESSED_TASKS_PATH = CONFIG_DIR / "processed-tasks.json"

REFRESH_INTERVAL_S = 2.0

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
        "apiBaseUrl": file_config.get("apiBaseUrl") or os.environ.get("SLASH_PC_RUNNER_API_BASE_URL", "http://localhost:4000"),
        "pairingCode": file_config.get("pairingCode") or os.environ.get("SLASH_PC_RUNNER_PAIRING_CODE"),
        "deviceName": file_config.get("deviceName") or os.environ.get("SLASH_PC_RUNNER_DEVICE_NAME", "slash-pc-runner-py"),
        "heartbeatIntervalS": float(
            file_config.get("heartbeatIntervalS") or os.environ.get("SLASH_PC_RUNNER_HEARTBEAT_INTERVAL_S", 30)
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
        data=json.dumps({"email": "pc-runner-py-app-tester@example.com", "displayName": "pc-runner-py-app tester"}).encode(),
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


class TrayApp:
    def __init__(self):
        icon_path = resource_path("assets", "trayIcon.png")
        image = Image.open(icon_path) if icon_path.exists() else Image.new("RGBA", (16, 16), (0, 0, 0, 0))

        self.agent: Optional[ContractPcRunner] = None
        self.file_index_store: Optional[FileIndexStore] = None
        self.current_config: dict = {}
        self._search_folders_mtime: Optional[float] = None
        self.folders_window_proc: Optional[subprocess.Popen] = None
        self._status_text = "상태: 연결 중..."
        self._device_text = "기기 ID: -"
        self._api_text = "mock-api: -"
        # 버전·커밋·빌드일자는 프로세스 실행 중 안 바뀌므로 한 번만 계산한다(_build_info.py
        # 참고). docker version/kubectl version처럼 줄을 나눠 보여준다 — 배포 문제
        # 재검증 요청 시 이 세 줄만 보고 정확히 어느 빌드인지, 최신인지 바로 확인할 수 있다.
        self._version_text = f"버전: {PACKAGE_VERSION}"
        self._commit_text = f"커밋: {get_build_sha()}"
        self._build_date_text = f"빌드: {get_build_date()}"
        # 앱 시작 시 한 번만 GitHub Releases를 조회한다(update_check.py 참고) — 확인
        # 전이거나 최신이면 None이라 이 줄 자체가 안 보인다. 메뉴를 열어야 보이는
        # 것만으로는 알림이라 부르기 부족해, 발견 시점에 icon.notify()로도 띄운다.
        self._update_text: Optional[str] = None
        self._update_url: Optional[str] = None
        self._stop_event = threading.Event()

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: self._status_text, None, enabled=False),
            pystray.MenuItem(lambda item: self._device_text, None, enabled=False),
            pystray.MenuItem(lambda item: self._api_text, None, enabled=False),
            pystray.MenuItem(lambda item: self._version_text, None, enabled=False),
            pystray.MenuItem(lambda item: self._commit_text, None, enabled=False),
            pystray.MenuItem(lambda item: self._build_date_text, None, enabled=False),
            pystray.MenuItem(
                lambda item: self._update_text or "",
                self.open_release_page,
                visible=lambda item: self._update_text is not None,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("색인 폴더 관리", self.open_folders_window),
            pystray.MenuItem("설정 폴더 열기", self.open_config_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self.quit_app),
        )
        self.icon = pystray.Icon("slash-pc-runner", icon=image, title="Slash", menu=menu)

    def start_agent(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_EXAMPLE_PATH.exists():
            CONFIG_EXAMPLE_PATH.write_text(
                json.dumps(
                    {"apiBaseUrl": "http://localhost:4000", "pairingCode": "123456", "deviceName": "내 PC", "heartbeatIntervalS": 30},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        self.current_config = _load_config()
        identity_store = KeyringIdentityStore(service_name="slash-pc-runner-py-app")
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

        def build_agent(code: Optional[str]) -> ContractPcRunner:
            return ContractPcRunner(
                ContractPcRunnerOptions(
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
        except DeviceRevokedError:
            # 해제된 기기는 자동 재등록을 시도하지 않는다 — 아래 fallback처럼 새 등록 코드를
            # 받아 다시 페어링하면 해제를 무시하고 새 기기로 재등록해버리는 꼴이 된다. 사용자가
            # 직접 재등록해야 한다는 신호이므로 그대로 전파한다(메시지에 서버 안내 문구가 담겨
            # 있어 setup()의 상태 표시에 그대로 노출된다).
            raise
        except Exception:
            # 저장된 식별 정보로 토큰 갱신까지 실패했는데 pairingCode도 없었던 경우(드묾) —
            # 새 등록 코드를 받아 한 번만 새로 페어링을 시도한다.
            if pairing_code or not has_persisted_identity:
                raise
            self.agent = build_agent(_obtain_pairing_code(api_base_url))
            self.agent.start()

    def _refresh_loop(self) -> None:
        while not self._stop_event.wait(REFRESH_INTERVAL_S):
            self.refresh()

    def _check_for_update_once(self) -> None:
        # 네트워크 호출이라 별도 스레드에서 한다 — setup()의 트레이 초기화(아이콘 표시,
        # Dock 숨김)를 이것 때문에 늦추면 안 된다.
        result = check_for_update()
        if result is not None and result.update_available:
            self._update_text = f"새 버전 있음: {result.latest_version}"
            self._update_url = result.release_url
            self.icon.update_menu()
            # 메뉴에 줄만 추가하면 사용자가 직접 열어 보지 않는 한 알아챌 방법이 없다 —
            # HAS_NOTIFICATION이 거짓인 플랫폼에서는 notify()가 조용히 아무 일도 안 한다.
            if self.icon.HAS_NOTIFICATION:
                self.icon.notify(f"{result.latest_version} 버전이 나왔습니다. 메뉴에서 눌러 다운로드하세요.", "Slash 업데이트")

    def open_release_page(self, icon, item) -> None:
        if self._update_url:
            webbrowser.open(self._update_url)

    def refresh(self) -> None:
        # 색인 폴더 관리 창은 같은 실행 파일을 인자만 바꿔 재사용한다(패키징 여부와 무관 —
        # __main__.py 주석 참고) — 그쪽 창이 떠 있는 동안 이 프로세스의 Dock 아이콘이 macOS
        # 쪽 사정으로 다시 나타나는 경우가 있어, 주기적으로 다시 눌러서 되돌린다.
        _hide_dock_icon()

        if self.agent is not None:
            state = self.agent.get_state()
            device_id = self.agent.get_device_id()
            self._status_text = f"상태: {STATE_LABEL.get(state, state)}"
            self._device_text = f"기기 ID: {(device_id[:8] + '…') if device_id else '-'}"
            self._api_text = f"mock-api: {self.current_config.get('apiBaseUrl', '-')}"
            self.icon.update_menu()

        # folders_window(별도 프로세스)가 search-folders.json을 바꿨는지 주기적으로 확인
        if self.file_index_store is not None and SEARCH_FOLDERS_PATH.exists():
            mtime = SEARCH_FOLDERS_PATH.stat().st_mtime
            if mtime != self._search_folders_mtime:
                self._search_folders_mtime = mtime
                folders = _load_search_folders()
                search_folders = [SearchFolderConfig(f["searchFolderId"], f["displayName"], f["rootPath"]) for f in folders]
                self.file_index_store.sync_folders(search_folders)

    def open_folders_window(self, icon, item) -> None:
        if not SEARCH_FOLDERS_PATH.exists():
            SEARCH_FOLDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SEARCH_FOLDERS_PATH.write_text(json.dumps(_load_search_folders(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.folders_window_proc = subprocess.Popen(_folders_window_command())

    def open_config_folder(self, icon, item) -> None:
        if sys.platform == "win32":
            subprocess.run(["explorer", str(CONFIG_DIR)])
        else:
            subprocess.run(["open", "-R", str(CONFIG_PATH)])

    def quit_app(self, icon, item) -> None:
        self._stop_event.set()
        if self.agent is not None:
            self.agent.stop()
        if self.file_index_store is not None:
            self.file_index_store.close()
        # 색인 폴더 관리 창은 별도 프로세스라 트레이가 죽어도 저절로 안 닫힌다 — 열려 있으면 같이 정리.
        if self.folders_window_proc is not None and self.folders_window_proc.poll() is None:
            self.folders_window_proc.terminate()
        icon.stop()

    def setup(self, icon) -> None:
        try:
            self.start_agent()
        except Exception as e:
            print(f"에이전트 시작 실패: {e}", file=sys.stderr)
            self._status_text = f"상태: 시작 실패 - {e}"
        # pystray가 백엔드(macOS는 NSApplication, Windows는 win32 메시지 루프)를 초기화한
        # *이후*에 이 함수가 별도 스레드로 호출된다 — folders_window.py가 pywebview에서 겪은
        # 것과 같은 종류의 순서 문제라, Dock 숨김도 여기서(icon.run() 진입 이후) 걸어야 한다.
        _hide_dock_icon()
        icon.visible = True
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        threading.Thread(target=self._check_for_update_once, daemon=True).start()


def _folders_window_command() -> list[str]:
    # 얼린 실행 파일은 진입점이 하나뿐이라(sys.executable이 파이썬 인터프리터가 아니라
    # 이 앱 자신이다), "--folders-window" 인자로 같은 실행 파일을 다시 불러 __main__.py가
    # 분기하게 한다. 개발 모드는 지금처럼 -m으로 모듈을 직접 지정한다.
    if is_frozen():
        return [sys.executable, "--folders-window", str(SEARCH_FOLDERS_PATH)]
    return [sys.executable, "-m", "slash_pc_runner.folders_window", str(SEARCH_FOLDERS_PATH)]


def _hide_dock_icon() -> None:
    # 패키징 전(Info.plist 없는 개발 모드)엔 macOS가 기본값으로 이 프로세스를 "Python"이라는
    # 이름과 기본 파이썬 아이콘으로 Dock에 띄운다 — 메뉴바 전용 앱이라 감춘다
    # (Electron agent-app의 app.dock.hide()와 동일한 목적). Windows에는 이 개념 자체가
    # 없으므로(AppKit import가 실패해) 아무 효과 없이 조용히 넘어간다.
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass


def main() -> None:
    if not single_instance.acquire():
        return
    app = TrayApp()
    app.icon.run(setup=app.setup)


if __name__ == "__main__":
    main()
