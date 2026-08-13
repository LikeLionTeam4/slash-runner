"""PyInstaller 전용 진입 스크립트.

Analysis()가 src/slash_pc_runner/__main__.py를 직접 가리키면 PyInstaller가 그 파일을
"slash_pc_runner 패키지 소속"이 아니라 독립 스크립트(__main__)로 취급해서, 그 안의 상대
임포트(from .tray_app import ...)가 "attempted relative import with no known parent
package"로 깨진다. 패키지 밖에서 절대 임포트로 불러와야 패키지 컨텍스트가 살아있다.
"""

from slash_pc_runner.__main__ import main

if __name__ == "__main__":
    main()
