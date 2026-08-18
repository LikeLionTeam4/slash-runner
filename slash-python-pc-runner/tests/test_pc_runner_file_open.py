"""FILE_OPEN TaskType 종단 시험 — fileRef로 지정한 파일을 실제로 실행하지 않고 파일
탐색기에서 위치만 표시하는지(WEB-P0B-03) 확인한다. 실제 Finder/탐색기를 띄우면 시험 환경에
따라 흔들리므로 reveal_in_file_manager는 항상 monkeypatch로 대체한다.
"""

import uuid

import pytest

from slash_pc_runner.agent import ContractPcRunner, ContractPcRunnerOptions
from slash_pc_runner.file_index import FileIndexStore, SearchFolderConfig

from fake_pc_runner_server import start_fake_pc_runner_server


@pytest.fixture
def server():
    s = start_fake_pc_runner_server()
    yield s
    s.close()


def start_agent_with_file(server, tmp_path_factory) -> tuple[ContractPcRunner, FileIndexStore, str, object]:
    """등록된 검색 폴더 안에 파일 하나를 두고 에이전트를 READY까지 올린 뒤,
    그 파일의 fileRef와 (root, relative_path)를 함께 돌려준다."""
    root = tmp_path_factory.mktemp("slash-pc-runner-file-open-test")
    (root / "문서.txt").write_text("")

    file_index_store = FileIndexStore(":memory:")
    search_folders = [SearchFolderConfig("sf-a", "테스트 폴더", str(root))]
    file_index_store.sync_folders(search_folders)
    file_ref = file_index_store.search("sf-a", "문서")["items"][0]["fileRef"]

    agent = ContractPcRunner(
        ContractPcRunnerOptions(
            api_base_url=server.url,
            pairing_code="000000",
            heartbeat_interval_s=60,
            search_folders=search_folders,
            file_index_store=file_index_store,
        )
    )
    agent.start()
    agent.wait_until_ready()
    return agent, file_index_store, file_ref, root


class TestFileOpen:
    def test_reveals_file_without_executing_it(self, server, tmp_path_factory, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "slash_pc_runner.agent.reveal_in_file_manager", lambda path: calls.append(path)
        )
        agent, file_index_store, file_ref, root = start_agent_with_file(server, tmp_path_factory)
        try:
            server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "FILE_OPEN", {"fileRef": file_ref})

            ack = server.wait_for_message("ACK")
            assert ack["accepted"] is True

            result = server.wait_for_message("RESULT")
            assert result["status"] == "SUCCEEDED"
            assert "revealedAt" in result["result"]
            assert calls == [(root / "문서.txt").resolve()]
        finally:
            agent.stop()
            file_index_store.close()

    def test_rejects_unknown_file_ref(self, server, tmp_path_factory, monkeypatch):
        monkeypatch.setattr(
            "slash_pc_runner.agent.reveal_in_file_manager", lambda path: pytest.fail("호출되면 안 된다")
        )
        agent, file_index_store, _file_ref, _root = start_agent_with_file(server, tmp_path_factory)
        try:
            server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "FILE_OPEN", {"fileRef": "변조된-ref"})

            ack = server.wait_for_message("ACK")
            assert ack["accepted"] is False
            assert ack["reasonCode"] == "FILE_NOT_FOUND"
        finally:
            agent.stop()
            file_index_store.close()

    def test_rejects_missing_file_ref_parameter(self, server, tmp_path_factory, monkeypatch):
        monkeypatch.setattr(
            "slash_pc_runner.agent.reveal_in_file_manager", lambda path: pytest.fail("호출되면 안 된다")
        )
        agent, file_index_store, _file_ref, _root = start_agent_with_file(server, tmp_path_factory)
        try:
            server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "FILE_OPEN", {})

            ack = server.wait_for_message("ACK")
            assert ack["accepted"] is False
            assert ack["reasonCode"] == "INVALID_PARAMETERS"
        finally:
            agent.stop()
            file_index_store.close()

    def test_rejects_file_ref_after_deregistering_folder(self, server, tmp_path_factory, monkeypatch):
        monkeypatch.setattr(
            "slash_pc_runner.agent.reveal_in_file_manager", lambda path: pytest.fail("호출되면 안 된다")
        )
        agent, file_index_store, file_ref, _root = start_agent_with_file(server, tmp_path_factory)
        try:
            file_index_store.sync_folders([])  # 검색 폴더 등록 해제 — LA-04 경계 재검증 대상

            server.send_task(str(uuid.uuid4()), str(uuid.uuid4()), "FILE_OPEN", {"fileRef": file_ref})

            ack = server.wait_for_message("ACK")
            assert ack["accepted"] is False
            assert ack["reasonCode"] == "FILE_NOT_FOUND"
        finally:
            agent.stop()
            file_index_store.close()
