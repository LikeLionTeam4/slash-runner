"""번들 리소스(아이콘·HTML·기본 색인 폴더) 경로 해석 — 개발 모드와 PyInstaller로 얼린
실행 파일 모드 둘 다에서 같은 방식으로 쓰기 위한 공통 헬퍼.

PyInstaller onefile 빌드는 실행 시점에 번들된 데이터 파일을 임시 디렉터리(sys._MEIPASS)에
풀어놓는다 — 얼린 상태에서는 __file__ 기준 상대경로 대신 그쪽을 봐야 한다.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_path(*parts: str) -> Path:
    """개발 모드: 이 패키지(src/slash_agent/) 기준. 얼린 모드: PyInstaller가 풀어둔 번들 루트 기준."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS"))
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def repo_fixtures_search_folder() -> Path:
    """개발 모드 기본 시드 폴더 — 이 저장소의 fixtures/search-folder.
    얼린 모드에서는 .spec이 번들 루트의 fixtures/search-folder로 같이 복사해 둔다."""
    if is_frozen():
        return resource_path("fixtures", "search-folder")
    return Path(__file__).resolve().parents[3] / "fixtures" / "search-folder"
