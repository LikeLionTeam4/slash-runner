"""FILE_OPEN TaskType 실행 — 파일 탐색기에서 해당 파일을 선택된 상태로 보여준다.

기본 연결 프로그램으로 파일을 직접 실행하지 않는다(WEB-P0B-03 결정: "위치 표시"만 —
검색 결과로 임의 파일을 실행하면, 그 파일이 실행 가능한 형식일 경우 사용자 확인 없이
코드가 실행되는 위험이 생긴다).
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def reveal_in_file_manager(path: Path) -> None:
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", "-R", str(path)], check=True, timeout=10)
    elif system == "Windows":
        # explorer.exe는 정상적으로 창을 띄우고도 0이 아닌 종료 코드를 반환하는 경우가 흔하다
        # (알려진 동작) — check=True를 쓰면 성공한 호출도 실패로 오판한다.
        subprocess.run(["explorer", f"/select,{path}"], timeout=10)
    else:
        raise RuntimeError(f"지원하지 않는 운영체제: {system}")
