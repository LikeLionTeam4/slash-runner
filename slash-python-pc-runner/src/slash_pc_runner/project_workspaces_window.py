"""프로젝트 폴더(CODE_ANALYSIS 대상) 관리 창 — folders_window.py와 같은 구조.

색인 폴더와 달리 workspaceType(GIT_REPOSITORY/DIRECTORY)·availableCodeAdapters는 사용자가
입력하지 않는다 — 새 폴더를 고르는 시점에 code_adapters.py의 자동 판정 로직으로 계산해
미리보기로만 보여주고, project-workspaces.json에는 workspaceId·displayName·rootPath만
저장한다(tray_app.py가 에이전트 시작 시 ProjectWorkspaceConfig.from_root_path()로 다시
계산한다 — 여기서 보여주는 값은 미리보기일 뿐 저장되는 값이 아니다. 특히
availableCodeAdapters는 이 PC에 어떤 CLI가 깔려 있는지를 반영할 뿐 폴더별로 다르지 않다).

실행: python -m slash_pc_runner.project_workspaces_window <project-workspaces.json 경로>
확인을 누르면 그 경로에 갱신된 목록을 쓰고 프로세스를 끝낸다(os._exit(0)), 취소는
아무것도 안 쓰고 끝낸다(os._exit(1)) — 이유는 pairing_window.py 상단 주석 참고
(window.destroy()가 JS 콜백 스레드에서 webview.start()의 이벤트 루프를 못 푸는 문제).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional
from uuid import uuid4

import webview

from .code_adapters import ProjectWorkspaceConfig
from .resources import resource_path

HTML_PATH = resource_path("project_workspaces_window.html")
HTML_CONTENT = HTML_PATH.read_text(encoding="utf-8")
APP_ICON_PATH = resource_path("assets", "AppIcon.ico" if sys.platform == "win32" else "AppIcon.icns")


def load_workspaces(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_workspaces(path: Path, workspaces: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workspaces, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_workspaces(workspaces: list[dict]) -> list[dict]:
    """save() 저장 직전 정규화 — os._exit() 호출과 분리해 단위 시험 가능하게 뺀다."""
    return [
        {
            "workspaceId": w.get("workspaceId") or f"ws-{uuid4().hex[:8]}",
            "displayName": w["displayName"],
            "rootPath": os.path.expanduser(w["rootPath"]),
        }
        for w in workspaces
    ]


def display_path(path: str) -> str:
    """folders_window.py의 같은 함수와 동일한 이유 — 홈 디렉터리 아래면 사용자 이름이
    그대로 보이지 않도록 ~로 줄인다(화면 표시만, 저장값은 절대경로 그대로)."""
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


class Api:
    def __init__(self, config_path: Path):
        self._config_path = config_path
        self._workspaces = load_workspaces(config_path)
        self.window: Optional[webview.Window] = None

    def get_workspaces(self) -> list[dict]:
        return [{**w, "rootPath": display_path(w["rootPath"])} for w in self._workspaces]

    def pick_folder(self) -> Optional[dict]:
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        root_path = result[0]
        # 저장하지 않는 미리보기 — 고른 폴더가 Git 저장소인지, 이 PC에 어떤 도구를 쓸 수
        # 있는지 확인용으로 실제 배선(agent.py)과 같은 판정 로직을 그대로 쓴다.
        preview = ProjectWorkspaceConfig.from_root_path("preview", "", root_path)
        return {
            "rootPath": root_path,
            "suggestedDisplayName": Path(root_path).name,
            "workspaceType": preview.workspace_type,
            "availableCodeAdapters": preview.available_code_adapters,
        }

    # window.destroy()에 의존하지 않는다 — pairing_window.py에서 실측으로 확인한 것과 같은
    # 문제가 여기도 있다(JS 콜백 스레드에서 부르면 창은 사라져도 webview.start()가 안 풀림).
    def save(self, workspaces: list[dict]) -> None:
        save_workspaces(self._config_path, normalize_workspaces(workspaces))
        os._exit(0)

    def cancel(self) -> None:
        os._exit(1)


def _hide_dock_icon() -> None:
    # folders_window.py의 같은 함수와 동일한 이유.
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass


def _activate_window() -> None:
    # pairing_window.py의 같은 함수와 동일한 이유 — Accessory 정책 앱은 새 창이 떠도
    # 자동으로 키 윈도우가 안 되는 경우가 있어 명시적으로 활성화한다.
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def run(config_path_str: str) -> None:
    """반환하지 않는다 — Api.save/cancel이나 창 닫기 핸들러가 os._exit()로 프로세스를
    직접 끝낸다(적용 0, 취소 1)."""
    api = Api(Path(config_path_str))
    window = webview.create_window(
        "프로젝트 폴더 관리",
        html=HTML_CONTENT,
        js_api=api,
        width=560,
        height=460,
        resizable=False,
        background_color="#0a0c14",
    )
    api.window = window
    window.events.closed += lambda: os._exit(1)
    _hide_dock_icon()
    _activate_window()
    webview.start(icon=str(APP_ICON_PATH) if APP_ICON_PATH.exists() else None)


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: python -m slash_pc_runner.project_workspaces_window <project-workspaces.json 경로>", file=sys.stderr)
        sys.exit(2)
    run(sys.argv[1])
    sys.exit(1)


if __name__ == "__main__":
    main()
