"""project_workspaces_window.py의 경로 표시·정규화 순수 함수 시험.

test_folders_window.py와 같은 이유로 webview 창 자체(Api.save/cancel, os._exit() 사용)는
여기서 직접 부르지 않는다 — 실제 창 동작은 수동으로 검증했다.
"""

from __future__ import annotations

from pathlib import Path

import slash_pc_runner.project_workspaces_window as project_workspaces_window


class TestDisplayPath:
    def test_shortens_path_under_home(self, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/testuser")))

        assert project_workspaces_window.display_path("/Users/testuser/dev/slash-runner") == "~/dev/slash-runner"

    def test_leaves_non_home_path_untouched(self, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/testuser")))

        assert project_workspaces_window.display_path("/opt/shared/repo") == "/opt/shared/repo"


class TestNormalizeWorkspaces:
    """save() 저장 직전 정규화 — ~로 줄인 표시값을 그대로 되돌려받아도(round-trip) 실제
    파일엔 절대경로가 남아야 한다(agent.py가 ~를 리터럴 경로로 오해하면 안 됨)."""

    def test_expands_tilde_back_to_absolute_path(self, monkeypatch):
        monkeypatch.setattr("os.path.expanduser", lambda p: p.replace("~", "/Users/testuser", 1))

        result = project_workspaces_window.normalize_workspaces(
            [{"workspaceId": "ws-1", "displayName": "내 프로젝트", "rootPath": "~/dev/slash-runner"}]
        )

        assert result == [
            {"workspaceId": "ws-1", "displayName": "내 프로젝트", "rootPath": "/Users/testuser/dev/slash-runner"}
        ]

    def test_generates_id_when_missing(self):
        result = project_workspaces_window.normalize_workspaces(
            [{"workspaceId": None, "displayName": "새 프로젝트", "rootPath": "/opt/repo"}]
        )

        assert result[0]["workspaceId"].startswith("ws-")

    def test_preserves_existing_id(self):
        result = project_workspaces_window.normalize_workspaces(
            [{"workspaceId": "ws-existing", "displayName": "기존", "rootPath": "/opt/repo"}]
        )

        assert result[0]["workspaceId"] == "ws-existing"

    def test_drops_preview_only_fields(self):
        # pick_folder()가 미리보기로 붙이는 workspaceType·availableCodeAdapters는 저장
        # 대상이 아니다 — 실제 판정은 agent.py가 시작할 때 다시 한다.
        result = project_workspaces_window.normalize_workspaces(
            [
                {
                    "workspaceId": None,
                    "displayName": "새 프로젝트",
                    "rootPath": "/opt/repo",
                    "workspaceType": "GIT_REPOSITORY",
                    "availableCodeAdapters": ["CLAUDE_CODE"],
                }
            ]
        )

        assert set(result[0].keys()) == {"workspaceId", "displayName", "rootPath"}
