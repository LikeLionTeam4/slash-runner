"""tray_app.py 회귀 시험 — 트레이 메뉴 버전·상태 표시가 영구히 빈 채로 남던 버그
(PR#32)의 재발을 막는다.

`pystray.Icon`을 대역으로 바꿔 실제 macOS/Windows 네이티브 백엔드를 안 건드리고, 그
위에서 setup()/_refresh_loop()가 실제로 하는 일(update_menu() 호출 시점, 예외가 나도
루프가 죽지 않는지)만 검증한다.
"""

from __future__ import annotations

import json
import threading

import pytest

import slash_pc_runner.tray_app as tray_app


class FakeIcon:
    """pystray.Icon 대역 — 네이티브 메뉴를 그리지 않고 호출만 기록한다."""

    def __init__(self, name, icon=None, title=None, menu=None):
        self.name = name
        self.menu = menu
        self.visible = False
        self.update_menu_calls = 0
        self.notifications: list[tuple[str, str]] = []
        self.HAS_NOTIFICATION = True

    def update_menu(self) -> None:
        self.update_menu_calls += 1

    def notify(self, message: str, title: str = "") -> None:
        self.notifications.append((message, title))


class FakeStopEvent:
    """threading.Event 대역 — 실제로 기다리지 않고 정해진 횟수만큼만 루프를 돈다."""

    def __init__(self, iterations: int):
        self._remaining = iterations

    def wait(self, timeout: float | None = None) -> bool:
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(tray_app.pystray, "Icon", FakeIcon)
    monkeypatch.setattr(tray_app, "_hide_dock_icon", lambda: None)
    # 백그라운드 스레드가 실제로 뜨면(네트워크 호출·무한 루프) 시험이 느려지고 격리가
    # 깨진다 — setup()이 무엇을 호출하는지만 보면 되므로 스레드 시작 자체를 대역으로 바꾼다.
    monkeypatch.setattr(tray_app.threading, "Thread", lambda target, daemon: FakeThread(target))
    return tray_app.TrayApp()


class FakeThread:
    """threading.Thread 대역 — start()해도 별도 스레드를 띄우지 않는다."""

    def __init__(self, target):
        self.target = target

    def start(self) -> None:
        pass


class TestSetupAlwaysUpdatesMenu:
    """PR#32에서 고친 것 — update_menu()가 self.agent 상태와 무관하게 최소 한 번은
    불려야, 버전·커밋·빌드 같은 정적 텍스트가 첫 화면에 바로 보인다."""

    def test_updates_menu_when_start_agent_succeeds(self, app, monkeypatch):
        monkeypatch.setattr(app, "start_agent", lambda: None)

        app.setup(app.icon)

        assert app.icon.update_menu_calls >= 1

    def test_updates_menu_even_when_start_agent_raises(self, app, monkeypatch):
        """실측 재현 사례 — 키체인 접근이 거부되면 start_agent()가 예외를 던진다.
        이 경우에도 메뉴는 정적 텍스트를 보여줘야 한다(버그 당시엔 여기서 실패했다)."""

        def failing_start_agent():
            raise RuntimeError("Can't get password from keychain: (-128, 'Keychain Access Denied')")

        monkeypatch.setattr(app, "start_agent", failing_start_agent)

        app.setup(app.icon)

        assert app.icon.update_menu_calls >= 1
        assert "Keychain Access Denied" in app._status_text

    def test_icon_becomes_visible_regardless_of_agent_outcome(self, app, monkeypatch):
        monkeypatch.setattr(app, "start_agent", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        app.setup(app.icon)

        assert app.icon.visible is True


class TestRefreshLoopSurvivesExceptions:
    """_refresh_loop()가 refresh() 예외로 조용히 죽으면, self.agent가 있어도 상태·기기ID가
    __init__ 기본값에 영원히 멈춘다 — 같은 증상을 만드는 별개의 경로라 함께 고쳤다."""

    def test_continues_after_single_exception(self, app, monkeypatch):
        app._stop_event = FakeStopEvent(iterations=2)
        calls = []

        def flaky_refresh():
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                raise RuntimeError("일시적 오류")

        monkeypatch.setattr(app, "refresh", flaky_refresh)

        app._refresh_loop()

        assert calls == [1, 2]

    def test_stops_when_event_is_set(self, app, monkeypatch):
        app._stop_event = FakeStopEvent(iterations=0)
        calls = []
        monkeypatch.setattr(app, "refresh", lambda: calls.append(1))

        app._refresh_loop()

        assert calls == []


class TestLoadConfigApiBaseUrlDefault:
    """config.json도 환경변수도 없을 때의 apiBaseUrl 기본값 — 얼린 앱(배포판)이 로컬
    mock-api 전용 주소(localhost:4000)로 접속을 시도하다 원인 불명의 "연결 거부"만 보던
    문제(Windows 실기기에서 재현)를 고친 부분이다. 개발 모드는 `/test/login` 자동 페어링이
    로컬 mock-api를 전제로 하므로 기존 기본값을 그대로 유지해야 한다."""

    def _clear_env(self, monkeypatch):
        for name in (
            "SLASH_PC_RUNNER_API_BASE_URL",
            "SLASH_PC_RUNNER_PAIRING_CODE",
            "SLASH_PC_RUNNER_DEVICE_NAME",
            "SLASH_PC_RUNNER_HEARTBEAT_INTERVAL_S",
        ):
            monkeypatch.delenv(name, raising=False)

    def test_frozen_defaults_to_dev_server(self, tmp_path, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setattr(tray_app, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(tray_app, "is_frozen", lambda: True)

        assert tray_app._load_config()["apiBaseUrl"] == "https://api.dev.sbsh.cloud"

    def test_dev_mode_defaults_to_local_mock_api(self, tmp_path, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setattr(tray_app, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(tray_app, "is_frozen", lambda: False)

        assert tray_app._load_config()["apiBaseUrl"] == "http://localhost:4000"

    def test_config_file_value_wins_regardless_of_frozen(self, tmp_path, monkeypatch):
        self._clear_env(monkeypatch)
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"apiBaseUrl": "https://api.example.com"}), encoding="utf-8")
        monkeypatch.setattr(tray_app, "CONFIG_PATH", path)
        monkeypatch.setattr(tray_app, "is_frozen", lambda: True)

        assert tray_app._load_config()["apiBaseUrl"] == "https://api.example.com"


class TestLoadProjectWorkspaces:
    """CODE_ANALYSIS가 실제 AWS 환경에서 WORKSPACE_NOT_FOUND로 끝나던 원인 — agent.py는
    project_workspaces를 이미 완전히 지원했지만 tray_app.py가 그 값을 읽어서 넘기지 않았다.
    search_folders와 달리 데모용 기본값은 없다(README 참고 — 실제 로컬 프로젝트가 있어야
    의미 있는 기능이라 억지 시드 데이터를 만들지 않는다)."""

    def test_returns_empty_list_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tray_app, "PROJECT_WORKSPACES_PATH", tmp_path / "project-workspaces.json")

        assert tray_app._load_project_workspaces() == []

    def test_reads_configured_workspaces(self, tmp_path, monkeypatch):
        path = tmp_path / "project-workspaces.json"
        path.write_text(
            json.dumps([{"workspaceId": "w1", "displayName": "내 프로젝트", "rootPath": str(tmp_path)}]),
            encoding="utf-8",
        )
        monkeypatch.setattr(tray_app, "PROJECT_WORKSPACES_PATH", path)

        assert tray_app._load_project_workspaces() == [
            {"workspaceId": "w1", "displayName": "내 프로젝트", "rootPath": str(tmp_path)}
        ]

    def test_returns_empty_list_on_invalid_json(self, tmp_path, monkeypatch):
        path = tmp_path / "project-workspaces.json"
        path.write_text("{invalid", encoding="utf-8")
        monkeypatch.setattr(tray_app, "PROJECT_WORKSPACES_PATH", path)

        assert tray_app._load_project_workspaces() == []
