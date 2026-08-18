"""fileIndexStore.test.ts 대응 — 다중 폴더 SQLite/FTS5 색인 단위 시험.

WSS·agent.py 없이 FileIndexStore 자체만 검증(색인·검색·증분 감시).
"""

import tempfile
import time
from pathlib import Path

import pytest

from slash_pc_runner.file_index import FileIndexStore, SearchFolderConfig


def make_tmp_folder(tmp_path_factory, files: dict[str, str]) -> Path:
    root = tmp_path_factory.mktemp("slash-pc-runner-index-test")
    for name, content in files.items():
        # encoding 생략 시 Path.write_text()는 로케일 기본 인코딩을 쓴다 — 한글 Windows(cp949)에서
        # "수정됨".encode()(기본 UTF-8, 9바이트)와 실제 쓰여진 바이트 수(cp949, 6바이트)가 어긋나
        # sizeBytes 비교 assert가 항상 실패했다. 명시적으로 맞춰준다.
        (root / name).write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def store():
    s = FileIndexStore(":memory:")
    yield s
    s.close()


def wait_until(predicate, timeout_s: float = 5.0, interval_s: float = 0.1):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval_s)
    assert predicate(), "조건이 시간 내에 충족되지 않았습니다"


class TestSearchResults:
    def test_matches_korean_and_english_filenames_by_substring(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"프로젝트_계획.md": "", "readme.txt": "", "다른파일.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])

        hit = store.search("sf-a", "프로젝트")
        assert [i["name"] for i in hit["items"]] == ["프로젝트_계획.md"]

        hit2 = store.search("sf-a", "readme")
        assert [i["name"] for i in hit2["items"]] == ["readme.txt"]

    def test_short_query_falls_back_to_like(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"ab.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])

        assert [i["name"] for i in store.search("sf-a", "ab")["items"]] == ["ab.txt"]

    def test_truncated_when_over_limit(self, tmp_path_factory, store):
        files = {f"match-{i}.txt": "" for i in range(5)}
        root = make_tmp_folder(tmp_path_factory, files)
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])

        result = store.search("sf-a", "match", limit=3)
        assert len(result["items"]) == 3
        assert result["truncated"] is True


class TestMultiFolderIsolation:
    def test_results_from_one_folder_do_not_leak_into_another(self, tmp_path_factory, store):
        root_a = make_tmp_folder(tmp_path_factory, {"공유이름.txt": ""})
        root_b = make_tmp_folder(tmp_path_factory, {"공유이름.txt": ""})
        store.sync_folders(
            [
                SearchFolderConfig("sf-a", "A", str(root_a)),
                SearchFolderConfig("sf-b", "B", str(root_b)),
            ]
        )

        assert len(store.search("sf-a", "공유이름")["items"]) == 1
        assert len(store.search("sf-b", "공유이름")["items"]) == 1


class TestFolderStatus:
    def test_missing_path_reports_unavailable_and_unsearchable(self, tmp_path_factory, store):
        store.sync_folders([SearchFolderConfig("sf-missing", "없음", "/no/such/path/slash-pc-runner-test")])

        assert store.list_search_folders() == [
            {"searchFolderId": "sf-missing", "displayName": "없음", "indexStatus": "UNAVAILABLE"}
        ]
        assert store.is_searchable("sf-missing") is False

    def test_valid_folder_becomes_indexed_after_initial_scan(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"a.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])

        assert store.list_search_folders() == [{"searchFolderId": "sf-a", "displayName": "A", "indexStatus": "INDEXED"}]

    def test_folder_removed_from_config_disappears_from_list(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"a.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])
        store.sync_folders([])

        assert store.list_search_folders() == []
        assert store.is_searchable("sf-a") is False


class TestFileRef:
    def test_search_result_includes_stable_unique_file_ref(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"a.txt": "", "b.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])

        first = {i["name"]: i["fileRef"] for i in store.search("sf-a", "txt")["items"]}
        assert first["a.txt"] != first["b.txt"]
        assert all(isinstance(v, str) and v for v in first.values())

        # 같은 파일을 다시 색인해도(재시작 시나리오 재현 — sync_folders 재호출) file_ref는 유지된다.
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])
        second = {i["name"]: i["fileRef"] for i in store.search("sf-a", "txt")["items"]}
        assert first == second

    def test_modifying_file_keeps_same_file_ref(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"a.txt": "원본"})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])
        before = store.search("sf-a", "a.txt")["items"][0]["fileRef"]

        (root / "a.txt").write_text("수정됨", encoding="utf-8")
        wait_until(lambda: store.search("sf-a", "a.txt")["items"][0]["sizeBytes"] == len("수정됨".encode()))

        after = store.search("sf-a", "a.txt")["items"][0]["fileRef"]
        assert before == after

    def test_resolve_file_ref_returns_absolute_path(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"a.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])
        file_ref = store.search("sf-a", "a.txt")["items"][0]["fileRef"]

        resolved = store.resolve_file_ref(file_ref)
        assert resolved == (root / "a.txt").resolve()

    def test_resolve_file_ref_returns_none_for_unknown_ref(self, store):
        assert store.resolve_file_ref("존재하지-않는-ref") is None

    def test_resolve_file_ref_returns_none_after_file_deleted(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"지울파일.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])
        file_ref = store.search("sf-a", "지울파일")["items"][0]["fileRef"]

        (root / "지울파일.txt").unlink()
        wait_until(lambda: store.resolve_file_ref(file_ref) is None, timeout_s=10)

    def test_resolve_file_ref_returns_none_when_folder_unregistered(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"a.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])
        file_ref = store.search("sf-a", "a.txt")["items"][0]["fileRef"]

        store.sync_folders([])  # 검색 폴더 등록 해제 — 색인 DB의 행 자체는 남아있어도 거부해야 한다

        assert store.resolve_file_ref(file_ref) is None


class TestIncrementalWatch:
    def test_finds_file_added_after_initial_scan(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])

        (root / "나중추가.txt").write_text("")

        wait_until(lambda: len(store.search("sf-a", "나중추가")["items"]) == 1, timeout_s=10)

    def test_deleted_file_no_longer_found(self, tmp_path_factory, store):
        root = make_tmp_folder(tmp_path_factory, {"지울파일.txt": ""})
        store.sync_folders([SearchFolderConfig("sf-a", "A", str(root))])

        assert len(store.search("sf-a", "지울파일")["items"]) == 1
        (root / "지울파일.txt").unlink()

        wait_until(lambda: len(store.search("sf-a", "지울파일")["items"]) == 0, timeout_s=10)
