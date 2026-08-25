"""_build_info.py 단위 시험 — 번들 파일 우선, 없으면 git 폴백, 그마저 실패하면 unknown."""

from slash_pc_runner import _build_info


def test_uses_bundled_file_when_present(tmp_path, monkeypatch):
    sha_file = tmp_path / "_build_sha.txt"
    sha_file.write_text("abc1234\n", encoding="utf-8")
    monkeypatch.setattr(_build_info, "resource_path", lambda name: tmp_path / name)

    assert _build_info.get_build_sha() == "abc1234"


def test_falls_back_to_git_when_bundled_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_build_info, "resource_path", lambda name: tmp_path / name)

    # 이 저장소 자체가 git 저장소이므로 실제 HEAD 짧은 SHA(7자리 hex)가 나와야 한다.
    sha = _build_info.get_build_sha()
    assert sha != "unknown"
    assert len(sha) == 7
    int(sha, 16)  # hex 문자열인지 확인 — 아니면 ValueError


def test_empty_bundled_file_falls_back_to_git(tmp_path, monkeypatch):
    (tmp_path / "_build_sha.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(_build_info, "resource_path", lambda name: tmp_path / name)

    assert _build_info.get_build_sha() != "unknown"


def test_returns_unknown_when_git_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(_build_info, "resource_path", lambda name: tmp_path / name)
    monkeypatch.setattr(_build_info, "_repo_root", lambda: tmp_path)  # git 저장소가 아닌 경로

    assert _build_info.get_build_sha() == "unknown"
    assert _build_info.get_build_date() == "unknown"


def test_build_date_format(tmp_path, monkeypatch):
    monkeypatch.setattr(_build_info, "resource_path", lambda name: tmp_path / name)

    date = _build_info.get_build_date()
    assert date != "unknown"
    assert len(date) == 8
    int(date)  # YYYYMMDD 숫자 형식인지 확인


def test_get_agent_version_combines_all_three(tmp_path, monkeypatch):
    monkeypatch.setattr(_build_info, "resource_path", lambda name: tmp_path / name)
    monkeypatch.setattr(_build_info, "get_build_sha", lambda: "abcdef1234567")
    monkeypatch.setattr(_build_info, "get_build_date", lambda: "20260819")

    assert _build_info.get_agent_version() == "slash-pc-runner-py/0.5.4+abcdef1.20260819"
