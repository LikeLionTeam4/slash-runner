"""다중 검색 폴더 색인 — fileIndexStore.ts 1:1 대응 (Electron/TypeScript 시절 원본, 저장소 Python 재작성으로 소멸).

파일명은 SQLite(표준 sqlite3 모듈) + FTS5(trigram)로, 변경 감시는 watchdog로 증분 반영한다.
better-sqlite3(Node)에 해당하는 서드파티 네이티브 확장 대신 표준 sqlite3를 쓰고, watchdog도
fsevents 없는 순수 wheel이라 PyInstaller 패키징 시 ABI 문제가 없다(FINDINGS.md에서 확인).

sqlite3 커넥션은 기본적으로 생성한 스레드에서만 써야 하는데, watchdog 이벤트는 자체 관찰자
스레드에서 오고 search()는 agent의 연결 루프 스레드에서 호출된다 — check_same_thread=False +
전 구간 Lock으로 직렬화해 두 스레드가 동시에 건드려도 안전하게 만든다.
"""

from __future__ import annotations

import sqlite3
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .protocol import KST


def _epoch_to_iso_kst(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(KST).isoformat(timespec="milliseconds")


@dataclass
class SearchFolderConfig:
    search_folder_id: str
    display_name: str
    root_path: str


@dataclass
class _TrackedFolder:
    search_folder_id: str
    display_name: str
    root_path: str
    status: str  # INDEXING / INDEXED / UNAVAILABLE
    observer: Optional[Observer] = None


class _FolderEventHandler(FileSystemEventHandler):
    def __init__(self, store: "FileIndexStore", folder_id: str, root: Path):
        self._store = store
        self._folder_id = folder_id
        self._root = root

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._store._upsert_file(self._folder_id, self._root, Path(event.src_path))

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._store._upsert_file(self._folder_id, self._root, Path(event.src_path))

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._store._remove_file(self._folder_id, self._root, Path(event.src_path))

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._store._remove_file(self._folder_id, self._root, Path(event.src_path))
            self._store._upsert_file(self._folder_id, self._root, Path(event.dest_path))


class FileIndexStore:
    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(
            """
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                folder_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_at TEXT NOT NULL,
                file_ref TEXT,
                UNIQUE(folder_id, relative_path)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                name, relative_path,
                content='files', content_rowid='id', tokenize='trigram'
            );

            CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, name, relative_path) VALUES (new.id, new.name, new.relative_path);
            END;
            CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, name, relative_path) VALUES ('delete', old.id, old.name, old.relative_path);
            END;
            CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, name, relative_path) VALUES ('delete', old.id, old.name, old.relative_path);
                INSERT INTO files_fts(rowid, name, relative_path) VALUES (new.id, new.name, new.relative_path);
            END;
            """
        )
        self._migrate_file_ref_column()
        self._conn.commit()
        self._folders: dict[str, _TrackedFolder] = {}

    def _migrate_file_ref_column(self) -> None:
        """이미 만들어진 DB(구버전 앱이 남긴 파일)에는 file_ref 컬럼이 없을 수 있다 —
        CREATE TABLE IF NOT EXISTS는 기존 테이블에 컬럼을 추가해주지 않으므로 직접 ALTER한다.
        기존 행은 이번에 한 번만 새 file_ref를 발급하고, 그 뒤로는 갱신 시에도 유지된다."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(files)").fetchall()}
        if "file_ref" not in columns:
            self._conn.execute("ALTER TABLE files ADD COLUMN file_ref TEXT")
        missing_ref_ids = [row[0] for row in self._conn.execute("SELECT id FROM files WHERE file_ref IS NULL").fetchall()]
        for file_id in missing_ref_ids:
            self._conn.execute("UPDATE files SET file_ref = ? WHERE id = ?", (str(uuid.uuid4()), file_id))
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_file_ref ON files(file_ref)")

    def sync_folders(self, configs: list[SearchFolderConfig]) -> None:
        """설정된 폴더 목록에 맞춰 감시를 맞춘다 — 새 폴더는 초기 스캔 시작, 빠진 폴더는 감시 해제."""
        desired_ids = {c.search_folder_id for c in configs}
        with self._lock:
            stale_ids = [fid for fid in self._folders if fid not in desired_ids]
        for folder_id in stale_ids:
            self._stop_watching(folder_id)
        for config in configs:
            self._ensure_watching(config)

    def _ensure_watching(self, config: SearchFolderConfig) -> None:
        with self._lock:
            existing = self._folders.get(config.search_folder_id)
            if existing and existing.root_path == config.root_path:
                existing.display_name = config.display_name
                return

        if existing is not None:
            self._stop_watching(config.search_folder_id)

        tracked = _TrackedFolder(
            search_folder_id=config.search_folder_id,
            display_name=config.display_name,
            root_path=config.root_path,
            status="INDEXING",
        )
        with self._lock:
            self._folders[config.search_folder_id] = tracked

        root = Path(config.root_path)
        if not root.exists():
            tracked.status = "UNAVAILABLE"
            return

        try:
            # watchdog는 감시 시작 이후 변경만 감지한다 — 기존 파일은 직접 한 번 훑어야 한다
            # (chokidar의 ignoreInitial:false에 해당하는 동작을 수동으로 재현)
            for path in root.rglob("*"):
                if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts):
                    self._upsert_file(config.search_folder_id, root, path)
        except OSError:
            tracked.status = "UNAVAILABLE"
            return

        handler = _FolderEventHandler(self, config.search_folder_id, root)
        observer = Observer()
        observer.schedule(handler, str(root), recursive=True)
        observer.start()
        tracked.observer = observer
        tracked.status = "INDEXED"

    def _stop_watching(self, folder_id: str) -> None:
        with self._lock:
            tracked = self._folders.pop(folder_id, None)
        if tracked and tracked.observer:
            tracked.observer.stop()
            tracked.observer.join(timeout=5)

    # macOS(HFS+/APFS)는 조합 가능한 문자가 든 파일명을 NFD(자모 분리형)로 돌려준다.
    # 검색어는 보통 NFC(완성형)로 들어오니, 저장 시점에 NFC로 맞춰야 나중에 비교가 어긋나지 않는다.
    def _upsert_file(self, folder_id: str, root: Path, full_path: Path) -> None:
        try:
            stats = full_path.stat()
            relative_path = unicodedata.normalize("NFC", str(full_path.relative_to(root)))
            name = unicodedata.normalize("NFC", full_path.name)
            modified_at = _epoch_to_iso_kst(stats.st_mtime)
        except (OSError, ValueError):
            # 스캔·이벤트 사이에 파일이 없어지는 경합 — 뒤이어 오는 delete 이벤트가 정리한다
            return
        with self._lock:
            # file_ref는 새로 들어오는 행에만 쓰인다 — ON CONFLICT의 SET 절이 file_ref를 언급하지
            # 않으므로, 이미 있던 행(수정된 파일)은 기존 file_ref를 그대로 유지한다. FILE_OPEN이
            # 이 값으로 파일을 다시 찾으므로, 내용이 바뀌어도 같은 참조를 계속 쓸 수 있어야 한다.
            self._conn.execute(
                """INSERT INTO files (folder_id, relative_path, name, size_bytes, modified_at, file_ref)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(folder_id, relative_path) DO UPDATE SET
                     name = excluded.name, size_bytes = excluded.size_bytes, modified_at = excluded.modified_at""",
                (folder_id, relative_path, name, stats.st_size, modified_at, str(uuid.uuid4())),
            )
            self._conn.commit()

    def _remove_file(self, folder_id: str, root: Path, full_path: Path) -> None:
        try:
            relative_path = unicodedata.normalize("NFC", str(full_path.relative_to(root)))
        except ValueError:
            return
        with self._lock:
            self._conn.execute(
                "DELETE FROM files WHERE folder_id = ? AND relative_path = ?", (folder_id, relative_path)
            )
            self._conn.commit()

    def is_searchable(self, folder_id: str) -> bool:
        """설정된 폴더인지 + 검색 가능한 상태(UNAVAILABLE 아님)인지."""
        with self._lock:
            tracked = self._folders.get(folder_id)
            return tracked is not None and tracked.status != "UNAVAILABLE"

    def search(self, folder_id: str, query: str, limit: int = 20) -> dict:
        """
        trigram 토크나이저는 3글자 미만은 구문 검색이 안 맞아서, 짧은 검색어는 LIKE로 대체한다.
        MATCH 절은 항상 바인딩 파라미터로 넘긴다 — 검색어를 SQL 문자열에 직접 이어붙이면 FTS5
        쿼리 문법(따옴표·연산자)을 검색어로 위장해 주입할 수 있다.
        """
        normalized = unicodedata.normalize("NFC", query).lower()
        with self._lock:
            if len(normalized) >= 3:
                phrase = '"' + normalized.replace('"', '""') + '"'
                rows = self._conn.execute(
                    """SELECT f.name, f.relative_path, f.size_bytes, f.modified_at, f.file_ref
                       FROM files_fts JOIN files f ON f.id = files_fts.rowid
                       WHERE files_fts MATCH ? AND f.folder_id = ?
                       LIMIT ?""",
                    (phrase, folder_id, limit + 1),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT name, relative_path, size_bytes, modified_at, file_ref
                       FROM files WHERE folder_id = ? AND lower(name) LIKE '%' || ? || '%'
                       LIMIT ?""",
                    (folder_id, normalized, limit + 1),
                ).fetchall()

        truncated = len(rows) > limit
        items = [
            {
                "fileRef": row[4],
                "name": row[0],
                "relativePath": row[1],
                "extension": Path(row[0]).suffix.lstrip("."),
                "sizeBytes": row[2],
                "modifiedAt": row[3],
            }
            for row in rows[:limit]
        ]
        return {
            "searchFolderId": folder_id,
            "query": query,
            "items": items,
            "returnedCount": len(items),
            "truncated": truncated,
        }

    def resolve_file_ref(self, file_ref: str) -> Optional[Path]:
        """fileRef가 가리키는 파일의 절대 경로를 돌려준다. FILE_OPEN 실행에만 쓰고, 결과로
        클라우드에 그대로 돌려보내지 않는다(Root 절대경로 비노출 원칙). 다음 중 하나라도
        해당하면 None: ① 그런 file_ref가 없음(변조되었거나 이미 삭제된 파일), ② 그 폴더가
        더 이상 추적 대상이 아니거나 UNAVAILABLE(등록 해제·경로 소실), ③ 색인 당시와 달리
        지금 시점에는 등록 Root 밖을 가리킴(symlink 등) — LA-04 "매 실행 시 경계 재검증"에
        해당하는 방어 검사.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT folder_id, relative_path FROM files WHERE file_ref = ?", (file_ref,)
            ).fetchone()
            if row is None:
                return None
            folder_id, relative_path = row
            tracked = self._folders.get(folder_id)
        if tracked is None or tracked.status == "UNAVAILABLE":
            return None
        root = Path(tracked.root_path).resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def list_search_folders(self) -> list[dict]:
        """READY.searchFolders에 그대로 실어보낼 현재 폴더 상태 목록."""
        with self._lock:
            return [
                {"searchFolderId": f.search_folder_id, "displayName": f.display_name, "indexStatus": f.status}
                for f in self._folders.values()
            ]

    def close(self) -> None:
        """감시자·DB 핸들 정리 — 소유권은 호출부(cli.py/tray_app.py/테스트)에 있으니 여기서 자동 호출 안 함."""
        with self._lock:
            folder_ids = list(self._folders.keys())
        for folder_id in folder_ids:
            self._stop_watching(folder_id)
        with self._lock:
            self._conn.close()
