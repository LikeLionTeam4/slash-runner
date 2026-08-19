"""PC 등록 코드 입력 창 — folders_window.py와 같은 이유로 별도 프로세스(pywebview)로 띄운다.

배포판(PyInstaller로 얼린 실행 파일)은 개발용 `/test/login` 자동 페어링을 쓸 수 없어서
(tray_app.py의 is_frozen 분기 참고), 사용자가 웹 화면에서 발급받은 6자리 등록 코드를
여기서 직접 입력해야 한다.

실제 페어링 시도(서버 호출)는 이 창의 몫이 아니다 — 여기서는 형식이 맞는 6자리 코드를
받아 반환하기만 하고, 실제 성공·실패는 호출부(tray_app.py)가 agent.start()로 판단한다.
코드가 만료됐거나 이미 쓰였으면 호출부가 오류 메시지를 담아 이 창을 다시 띄운다.

실행: python -m slash_pc_runner.pairing_window [이전 시도 오류 메시지]
확인을 누르면 입력한 코드를 표준출력에 한 줄로 찍고 종료(exit 0), 취소는 아무것도
찍지 않고 종료(exit 1).
"""

from __future__ import annotations

import html
import sys
from typing import Optional

import webview

from .resources import resource_path

HTML_PATH = resource_path("pairing_window.html")
# file://로 넘기면 특정 조건에서 WKWebView가 조용히 빈 화면만 띄우는 문제가 있어(실측,
# folders_window.py에 남긴 것과 같은 이유) html=로 문자열 자체를 넘긴다.
HTML_CONTENT = HTML_PATH.read_text(encoding="utf-8")
APP_ICON_PATH = resource_path("assets", "AppIcon.ico" if sys.platform == "win32" else "AppIcon.icns")


class Api:
    def __init__(self):
        self.window: Optional[webview.Window] = None
        self.code: Optional[str] = None

    def submit(self, code: str) -> None:
        self.code = code
        self.window.destroy()

    def cancel(self) -> None:
        self.window.destroy()


def _hide_dock_icon() -> None:
    # folders_window.py의 같은 함수와 동일한 이유 — create_window() 이후에 다시 덮어써야
    # pywebview cocoa 백엔드가 강제로 건 Regular 정책을 되돌릴 수 있다.
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass


def run(error_message: Optional[str] = None) -> Optional[str]:
    """확인 시 입력한 6자리 코드, 취소 시 None을 돌려준다."""
    api = Api()
    # 변수명을 html_content로 둔다 — html 모듈(위 import)과 이름이 겹치면 이 함수
    # 안에서 지역변수가 모듈을 가려 html.escape() 호출이 깨진다.
    html_content = HTML_CONTENT
    if error_message:
        # 별도 템플릿 엔진 없이 플레이스홀더 하나만 채운다 — 서버가 보낸 오류 문자열을
        # 그대로 꽂으므로 html.escape()로 5개 표준 개체 전부 이스케이프한다(& " ' < >).
        html_content = html_content.replace("{{ERROR_MESSAGE}}", html.escape(error_message))
    else:
        html_content = html_content.replace("{{ERROR_MESSAGE}}", "")

    api.window = webview.create_window(
        "PC 등록",
        html=html_content,
        js_api=api,
        width=360,
        height=280,
        resizable=False,
        background_color="#0a0c14",
    )
    _hide_dock_icon()
    webview.start(icon=str(APP_ICON_PATH) if APP_ICON_PATH.exists() else None)
    return api.code


def main() -> None:
    error_message = sys.argv[1] if len(sys.argv) >= 2 else None
    code = run(error_message)
    if code is None:
        sys.exit(1)
    print(code)
    sys.exit(0)


if __name__ == "__main__":
    main()
