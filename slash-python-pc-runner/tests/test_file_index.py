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
        (root / name).write_text(content)
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
