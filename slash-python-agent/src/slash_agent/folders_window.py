"""색인 폴더 관리 창 — searchFoldersWindow.html+preload.cjs+IPC 핸들러 대응.

트레이(pystray)와 GUI 창은 둘 다 메인 스레드의 이벤트 루프가 필요해서 같은 프로세스
안에서 같이 못 돌린다 — 그래서 이 창은 별도 프로세스로 띄우고, search-folders.json 파일을
공유 인터페이스로 삼는다. tray_app.py는 이 파일의 mtime 변화를 주기적으로 감지해 반영한다
(실행 중인 색인 상태는 여기서 보여주지 않는다 — 그 상태는 트레이 메뉴 쪽 몫).

렌더링은 tkinter 네이티브 위젯 대신 pywebview(WKWebView)로 한다 — Electron 없이도
slash-web과 같은 실제 HTML/CSS(디자인 토큰까지 그대로)를 쓸 수 있고, 시스템 내장
WebView라 Chromium을 따로 안 묶어도 돼서 이 프로젝트의 "가볍게" 방향과도 맞는다.

실행: python -m slash_agent.folders_window <search-folders.json 경로>
확인을 누르면 그 경로에 갱신된 목록을 쓰고 종료(exit 0), 취소는 아무것도 안 쓰고 종료(exit 1).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional
from uuid import uuid4

import webview

from .resources import resource_path

HTML_PATH = resource_path("folders_window.html")
# url=file://...로 넘기면 macOS(cocoa.py)가 loadRequest_()로 그냥 쏘는데, 이건 WKWebView가
# 특정 조건(패키징된 ad-hoc 서명 앱 등)에서 조용히 실패해 완전히 빈 창만 뜬다 — 실측으로 확인한
# 버그(색인 폴더 창이 검은 화면만 뜨는 문제). html=로 문자열 자체를 넘기면 loadHTMLString_baseURL_
# 을 쓰는데, 이건 file:// 로딩 제약이 없어서 안전하다. 이 HTML은 외부 리소스 참조가 없어(전부
# 인라인 CSS/JS) 이렇게 바꿔도 잃는 게 없다.
HTML_CONTENT = HTML_PATH.read_text(encoding="utf-8")
# webview.start(icon=...)를 안 넘기면 WinForms(Windows)·Cocoa(macOS) 백엔드 둘 다
# sys.executable(개발 모드에서는 python.exe)에서 아이콘을 뽑아써서, 이 창의 작업표시줄/Dock
# 아이콘이 우리 로고가 아니라 파이썬 기본 아이콘으로 뜬다(winforms.py·cocoa.py 실측 확인).
APP_ICON_PATH = resource_path("assets", "AppIcon.ico" if sys.platform == "win32" else "AppIcon.icns")


def load_folders(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_folders(path: Path, folders: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8")


class Api:
    def __init__(self, config_path: Path):
        self._config_path = config_path
        self._folders = load_folders(config_path)
        self.window: Optional[webview.Window] = None
        self.applied = False

    def get_folders(self) -> list[dict]:
        return self._folders

    def pick_folder(self) -> Optional[dict]:
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        root_path = result[0]
        return {"rootPath": root_path, "suggestedDisplayName": Path(root_path).name}

    def save(self, folders: list[dict]) -> None:
        normalized = [
            {
                "searchFolderId": f.get("searchFolderId") or f"sf-{uuid4().hex[:8]}",
                "displayName": f["displayName"],
                "rootPath": f["rootPath"],
            }
            for f in folders
        ]
        save_folders(self._config_path, normalized)
        self.applied = True
        self.window.destroy()

    def cancel(self) -> None:
        self.window.destroy()


def _hide_dock_icon() -> None:
    # 패키징 전(Info.plist 없는 개발 모드)엔 macOS가 기본값으로 이 프로세스를 "Python"이라는
    # 이름과 기본 파이썬(로켓) 아이콘으로 Dock에 띄운다 — 메뉴바 트레이 하나로 충분하니 숨긴다.
    # pywebview의 cocoa 백엔드가 create_window() 내부(모듈 최초 임포트 시점)에서
    # setActivationPolicy_(Regular)를 자기가 먼저 강제로 걸어버리므로, 반드시 create_window()
    # 호출 *이후*에 다시 덮어써야 한다 — 이 함수를 start() 전에만 부르면 아무 효과가 없다.
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass


def run(config_path_str: str) -> int:
    """반환값 0 = 확인(적용), 1 = 취소 — __main__.py(얼린 모드 진입점)도 이 함수를 그대로 쓴다."""
    api = Api(Path(config_path_str))
    api.window = webview.create_window(
        "색인 폴더 관리",
        html=HTML_CONTENT,
        js_api=api,
        width=520,
        height=460,
        resizable=False,
        background_color="#0a0c14",
    )
    _hide_dock_icon()
    webview.start(icon=str(APP_ICON_PATH) if APP_ICON_PATH.exists() else None)
    return 0 if api.applied else 1


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: python -m slash_agent.folders_window <search-folders.json 경로>", file=sys.stderr)
        sys.exit(2)
    sys.exit(run(sys.argv[1]))


if __name__ == "__main__":
    main()
