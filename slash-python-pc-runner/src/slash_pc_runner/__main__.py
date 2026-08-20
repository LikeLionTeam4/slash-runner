"""단일 진입점 — `python -m slash_pc_runner`(개발) / PyInstaller가 얼리는 실행 파일(배포) 둘 다 여기로 들어온다.

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

        # run()은 반환하지 않는다 — Api.save/cancel이나 창 닫기 핸들러가 os._exit()로 이미
        # 프로세스를 끝낸다(folders_window.py 주석 참고). 여기 도달하면 이례적인 경우다.
        run(sys.argv[2])
        sys.exit(1)

    if len(sys.argv) >= 2 and sys.argv[1] == "--pairing-window":
        from .pairing_window import main as pairing_main

        sys.argv = sys.argv[1:]  # pairing_window.main()이 sys.argv[1]을 오류 메시지로 읽는다
        pairing_main()
        return

    from .tray_app import main as tray_main

    tray_main()


if __name__ == "__main__":
    main()
