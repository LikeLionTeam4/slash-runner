"""agent.file-search.test.ts 대응 — FILE_SEARCH가 다중 폴더 색인(FileIndexStore)을 실제로
거쳐 TASK→RESULT 왕복까지 이어지는지 확인. 색인 자체의 검색·증분 동작은 test_file_index.py.
"""

import uuid

import pytest

from slash_agent.agent import ContractAgent, ContractAgentOptions
from slash_agent.file_index import FileIndexStore, SearchFolderConfig

from fake_agent_server import start_fake_agent_server


@pytest.fixture
def server():
    s = start_fake_agent_server()
    yield s
    s.close()


def start_agent_with_folder(server, tmp_path_factory) -> tuple[ContractAgent, FileIndexStore]:
    root = tmp_path_factory.mktemp("slash-agent-file-search-test")
    (root / "프로젝트_계획.md").write_text("")

    file_index_store = FileIndexStore(":memory:")
    search_folders = [SearchFolderConfig("sf-a", "테스트 폴더", str(root))]
    file_index_store.sync_folders(search_folders)

    agent = ContractAgent(
        ContractAgentOptions(
            api_base_url=server.url,
            pairing_code="000000",
            heartbeat_interval_s=60,
            search_folders=search_folders,
            file_index_store=file_index_store,
        )
    )
    agent.start()
    agent.wait_until_ready()
    return agent, file_index_store


class TestReadySearchFolders:
    def test_reports_configured_folders_with_index_status(self, server, tmp_path_factory):
        agent, file_index_store = start_agent_with_folder(server, tmp_path_factory)
        try:
            ready = server.wait_for_message("READY")
            assert ready["searchFolders"] == [{"searchFolderId": "sf-a", "displayName": "테스트 폴더", "indexStatus": "INDEXED"}]
        finally:
            agent.stop()
            file_index_store.close()


class TestFileSearch:
    def test_finds_matching_file_in_registered_folder(self, server, tmp_path_factory):
        agent, file_index_store = start_agent_with_folder(server, tmp_path_factory)
        try:
            server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "FILE_SEARCH", {"query": "프로젝트", "searchFolderId": "sf-a"})

            result = server.wait_for_message("RESULT")
            assert result["status"] == "SUCCEEDED"
            items = result["result"]["items"]
            assert len(items) == 1
            assert items[0]["name"] == "프로젝트_계획.md"
            assert items[0]["relativePath"] == "프로젝트_계획.md"
            assert items[0]["sizeBytes"] == 0
            assert "modifiedAt" in items[0]
            assert result["result"]["returnedCount"] == 1
            assert result["result"]["truncated"] is False
        finally:
            agent.stop()
            file_index_store.close()

    def test_rejects_unknown_search_folder_id(self, server, tmp_path_factory):
        agent, file_index_store = start_agent_with_folder(server, tmp_path_factory)
        try:
            server.send_task(
                str(uuid.uuid4()), str(uuid.uuid4()), "FILE_SEARCH", {"query": "프로젝트", "searchFolderId": "sf-unknown"}
            )

            ack = server.wait_for_message("ACK")
            assert ack["accepted"] is False
            assert ack["reasonCode"] == "SEARCH_FOLDER_NOT_FOUND"
        finally:
            agent.stop()
            file_index_store.close()
