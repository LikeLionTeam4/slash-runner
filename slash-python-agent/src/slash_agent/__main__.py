"""단일 진입점 — `python -m slash_agent`(개발) / PyInstaller가 얼리는 실행 파일(배포) 둘 다 여기로 들어온다.

얼린 실행 파일은 엔트리 스크립트가 하나뿐이라, 트레이 앱 자신이 "색인 폴더 관리" 창을 별도
프로세스로 띄울 때도 같은 실행 파일을 인자만 다르게 줘서 재사용해야 한다(tray_app.py의
_folders_window_command 참고) — 그래서 트레이 기동과 색인 폴더 창 둘 다 이 파일 하나가
분기해서 처리한다.
"""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--folders-window":
        from .folders_window import run

        sys.exit(run(sys.argv[2]))

    from .tray_app import main as tray_main

    tray_main()


if __name__ == "__main__":
    main()
