"""folders_window.py의 경로 표시·정규화 순수 함수 시험.

webview 창 자체(Api.save/cancel)는 os._exit()로 프로세스를 끝내는 실제 GUI 동작이라
pytest 프로세스 안에서 직접 부를 수 없다 — 그래서 이 시험은 그 직전 단계인 경로 계산
로직만 검증한다(실제 창 동작은 수동으로 검증했다).
"""

from __future__ import annotations

import os
from pathlib import Path

import slash_pc_runner.folders_window as folders_window


class TestDisplayPath:
    """홈 디렉터리 아래 경로에서 사용자 이름이 그대로 노출되던 문제(fixtures/search-folder가
    패키징된 앱 안 절대경로로 보이던 것) 재발을 막는다.

    입력·기대값을 리터럴 문자열로 하드코딩하지 않고 fake_home에서 파생시킨다 — Windows에서
    Path("/Users/testuser")의 문자열 표현이 백슬래시가 되면서(os.sep 차이) 하드코딩된
    "/Users/testuser/..." 형태와 안 맞아 CI(windows-latest)에서만 실패했던 적이 있다.
    """

    def test_shortens_path_under_home(self, monkeypatch):
        fake_home = Path("/Users/testuser")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        target = str(fake_home / "Projects" / "demo")

        assert folders_window.display_path(target) == "~" + os.sep + "Projects" + os.sep + "demo"

    def test_exact_home_directory(self, monkeypatch):
        fake_home = Path("/Users/testuser")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        assert folders_window.display_path(str(fake_home)) == "~"

    def test_leaves_non_home_path_untouched(self, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/testuser")))

        assert folders_window.display_path("/opt/shared/demo") == "/opt/shared/demo"

    def test_does_not_shorten_unrelated_prefix_match(self, monkeypatch):
        # "/Users/testuser2"는 홈("/Users/testuser")로 시작하지 않는다 — 문자열 접두사만
        # 보면 잘못 걸릴 수 있어 os.sep 경계까지 확인하는지 검증한다.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/testuser")))

        assert folders_window.display_path("/Users/testuser2/demo") == "/Users/testuser2/demo"


class TestNormalizeFolders:
    """save() 저장 직전 정규화 — ~로 줄인 표시값을 그대로 되돌려받아도(round-trip) 실제
    파일엔 절대경로가 남아야 한다(FILE_SEARCH가 ~를 리터럴 경로로 오해하면 안 됨)."""

    def test_expands_tilde_back_to_absolute_path(self, monkeypatch):
        monkeypatch.setattr("os.path.expanduser", lambda p: p.replace("~", "/Users/testuser", 1))

        result = folders_window.normalize_folders(
            [{"searchFolderId": "sf-1", "displayName": "데모", "rootPath": "~/Projects/demo"}]
        )

        assert result == [{"searchFolderId": "sf-1", "displayName": "데모", "rootPath": "/Users/testuser/Projects/demo"}]

    def test_leaves_absolute_path_untouched(self):
        result = folders_window.normalize_folders(
            [{"searchFolderId": None, "displayName": "새 폴더", "rootPath": "/opt/shared/demo"}]
        )

        assert result[0]["rootPath"] == "/opt/shared/demo"

    def test_generates_id_when_missing(self):
        result = folders_window.normalize_folders(
            [{"searchFolderId": None, "displayName": "새 폴더", "rootPath": "/opt/shared/demo"}]
        )

        assert result[0]["searchFolderId"].startswith("sf-")

    def test_preserves_existing_id(self):
        result = folders_window.normalize_folders(
            [{"searchFolderId": "sf-existing", "displayName": "기존", "rootPath": "/opt/shared/demo"}]
        )

        assert result[0]["searchFolderId"] == "sf-existing"
