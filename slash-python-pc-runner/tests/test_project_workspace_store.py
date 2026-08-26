"""ProjectWorkspaceStore 단위 시험 (slash-runner#46).

이 저장소가 생긴 이유가 "관리 창에서 폴더를 바꿔도 실행 중인 에이전트에 반영되지
않는다"는 결함이라, 갱신이 실제로 보이는지를 중심으로 확인한다.
"""

from __future__ import annotations

import threading

from slash_pc_runner.code_adapters import ProjectWorkspaceConfig
from slash_pc_runner.project_workspace_store import ProjectWorkspaceStore


def make(workspace_id: str, adapters=("CLAUDE_CODE",)) -> ProjectWorkspaceConfig:
    return ProjectWorkspaceConfig(
        workspace_id=workspace_id,
        display_name=f"프로젝트 {workspace_id}",
        root_path=f"/tmp/{workspace_id}",
        workspace_type="DIRECTORY",
        available_code_adapters=list(adapters),
    )


class TestFind:
    def test_finds_registered_workspace(self):
        store = ProjectWorkspaceStore([make("w1")])

        assert store.find("w1") is not None
        assert store.find("w1").workspace_id == "w1"

    def test_returns_none_for_unknown_id(self):
        store = ProjectWorkspaceStore([make("w1")])

        assert store.find("없는id") is None

    def test_returns_none_when_empty(self):
        # 워크스페이스를 하나도 등록하지 않은 상태가 정상이다(데모용 기본값 없음).
        assert ProjectWorkspaceStore().find("w1") is None

    def test_returns_none_for_none_id(self):
        # TASK.parameters에 workspaceId가 아예 없으면 None이 들어온다.
        assert ProjectWorkspaceStore([make("w1")]).find(None) is None


class TestSyncWorkspaces:
    def test_added_workspace_is_visible_without_restart(self):
        """#46의 핵심 — 이게 실패하면 폴더를 추가해도 WORKSPACE_NOT_FOUND가 계속 난다."""
        store = ProjectWorkspaceStore()
        assert store.find("w1") is None

        store.sync_workspaces([make("w1")])

        assert store.find("w1") is not None

    def test_removed_workspace_disappears(self):
        store = ProjectWorkspaceStore([make("w1"), make("w2")])

        store.sync_workspaces([make("w2")])

        assert store.find("w1") is None
        assert store.find("w2") is not None

    def test_replaces_wholesale_not_merges(self):
        # sync는 병합이 아니라 통째 교체다 — 관리 창이 보낸 목록이 곧 전체 상태다.
        store = ProjectWorkspaceStore([make("w1")])

        store.sync_workspaces([])

        assert store.list_workspaces() == []

    def test_updates_available_adapters(self):
        # 나중에 codex를 설치하면 다음 갱신에서 어댑터 목록이 늘어난다.
        store = ProjectWorkspaceStore([make("w1", adapters=["CLAUDE_CODE"])])

        store.sync_workspaces([make("w1", adapters=["CLAUDE_CODE", "CODEX"])])

        assert store.find("w1").available_code_adapters == ["CLAUDE_CODE", "CODEX"]


class TestListWorkspaces:
    def test_shape_matches_ready_contract(self):
        store = ProjectWorkspaceStore([make("w1", adapters=["CLAUDE_CODE", "CODEX"])])

        assert store.list_workspaces() == [
            {
                "workspaceId": "w1",
                "displayName": "프로젝트 w1",
                "workspaceType": "DIRECTORY",
                "availableCodeAdapters": ["CLAUDE_CODE", "CODEX"],
            }
        ]

    def test_returns_copy_not_internal_reference(self):
        # 호출부가 결과를 만져도 저장소 상태가 바뀌면 안 된다.
        store = ProjectWorkspaceStore([make("w1")])

        listed = store.list_workspaces()
        listed[0]["availableCodeAdapters"].append("오염")

        assert store.find("w1").available_code_adapters == ["CLAUDE_CODE"]


class TestThreadSafety:
    def test_concurrent_sync_and_read_do_not_corrupt(self):
        """트레이 refresh 스레드가 sync하는 동안 연결 스레드가 find/list를 부른다."""
        store = ProjectWorkspaceStore([make("w1")])
        errors: list[Exception] = []
        stop = threading.Event()

        def writer():
            try:
                while not stop.is_set():
                    store.sync_workspaces([make("w1"), make("w2")])
                    store.sync_workspaces([make("w1")])
            except Exception as e:  # pragma: no cover - 실패 시에만 기록
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    store.find("w1")
                    store.list_workspaces()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        stop.wait(0.3)
        stop.set()
        for t in threads:
            t.join(timeout=2)

        assert errors == []
