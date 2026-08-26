"""CODE_ANALYSIS 대상 프로젝트 폴더의 살아있는 상태.

`file_index.py`의 `FileIndexStore`와 같은 역할이다 — 목록을 옵션 스냅샷이 아니라 이
객체가 들고 있어서, 프로젝트 폴더 관리 창에서 폴더를 추가하면 러너를 재시작하지 않아도
반영된다.

**왜 필요했나(`slash-runner#46`)**: 이전에는 `search_folders`만 상태 객체
(`FileIndexStore`)가 들고 있고 `project_workspaces`는 `ContractPcRunnerOptions`의 평범한
리스트라, `_build_ready()` 한 함수 안에서 한쪽은 실시간 조회·다른 쪽은 시작 시점 스냅샷을
쓰는 비대칭이 있었다. 그래서 폴더를 추가해도 실행 중인 에이전트는 옛 목록만 봤고
`WORKSPACE_NOT_FOUND`가 계속 났다.

`FileIndexStore`와 달리 영속 저장소(SQLite)나 감시 스레드가 없다 — 워크스페이스는 색인처럼
진행 상태를 가지지 않아서 메모리 상의 목록과 잠금만으로 충분하다. 원본 JSON 파일을 읽어
`sync_workspaces()`로 밀어 넣는 것은 호출부(`tray_app.py`)의 몫이며, 이것도
`search_folders`가 `sync_folders()`를 쓰는 방식과 같다.
"""

from __future__ import annotations

import threading
from typing import Iterable, Optional

from .code_adapters import ProjectWorkspaceConfig


class ProjectWorkspaceStore:
    def __init__(self, workspaces: Optional[Iterable[ProjectWorkspaceConfig]] = None) -> None:
        self._lock = threading.Lock()
        self._workspaces: list[ProjectWorkspaceConfig] = list(workspaces or [])

    def sync_workspaces(self, configs: Iterable[ProjectWorkspaceConfig]) -> None:
        """설정된 목록으로 통째로 교체한다.

        `FileIndexStore.sync_folders()`가 감시 대상을 맞추는 것과 달리 여기서는 정리할
        리소스(열린 파일·감시자)가 없어 교체만 하면 된다."""
        with self._lock:
            self._workspaces = list(configs)

    def list_workspaces(self) -> list[dict]:
        """READY.projectWorkspaces에 그대로 실어보낼 현재 목록."""
        with self._lock:
            return [
                {
                    "workspaceId": w.workspace_id,
                    "displayName": w.display_name,
                    "workspaceType": w.workspace_type,
                    "availableCodeAdapters": list(w.available_code_adapters),
                }
                for w in self._workspaces
            ]

    def find(self, workspace_id: Optional[str]) -> Optional[ProjectWorkspaceConfig]:
        """TASK의 workspaceId에 해당하는 설정. 없으면 None(호출부가 WORKSPACE_NOT_FOUND로 거절)."""
        with self._lock:
            return next((w for w in self._workspaces if w.workspace_id == workspace_id), None)
