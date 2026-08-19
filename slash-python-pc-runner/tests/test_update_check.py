"""update_check.py 단위 시험 — GitHub Releases 목록 조회 결과에 따라 최신 버전 비교."""

import json

from slash_pc_runner import update_check


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _releases_response(*tag_names: str) -> _FakeResponse:
    releases = [
        {"tag_name": tag, "html_url": f"https://github.com/LikeLionTeam4/slash-runner/releases/tag/{tag}"}
        for tag in tag_names
    ]
    return _FakeResponse(json.dumps(releases).encode("utf-8"))


def test_detects_update_available(monkeypatch):
    monkeypatch.setattr(update_check, "PACKAGE_VERSION", "0.3.0")
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: _releases_response("v0.4.0-pre"))

    result = update_check.check_for_update()

    assert result is not None
    assert result.update_available is True
    assert result.latest_version == "v0.4.0-pre"
    assert result.release_url == "https://github.com/LikeLionTeam4/slash-runner/releases/tag/v0.4.0-pre"


def test_no_update_when_current(monkeypatch):
    monkeypatch.setattr(update_check, "PACKAGE_VERSION", "0.4.0")
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: _releases_response("v0.4.0-pre"))

    result = update_check.check_for_update()

    assert result is not None
    assert result.update_available is False


def test_no_update_when_ahead_of_latest_release(monkeypatch):
    # dev에서 이미 버전을 올렸지만 아직 릴리스를 안 만든 경우 — 업데이트가 있다고
    # 잘못 알리면 안 된다.
    monkeypatch.setattr(update_check, "PACKAGE_VERSION", "0.5.0")
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: _releases_response("v0.4.0-pre"))

    result = update_check.check_for_update()

    assert result is not None
    assert result.update_available is False


def test_uses_most_recent_release_when_several_exist(monkeypatch):
    # /releases는 최신 생성 순으로 온다 — 맨 앞 것만 봐야 한다.
    monkeypatch.setattr(update_check, "PACKAGE_VERSION", "0.3.0")
    monkeypatch.setattr(
        update_check.urllib.request,
        "urlopen",
        lambda *a, **k: _releases_response("v0.4.0-pre", "v0.3.0-pre", "v0.2.0-pre"),
    )

    result = update_check.check_for_update()

    assert result is not None
    assert result.latest_version == "v0.4.0-pre"


def test_returns_none_on_network_failure(monkeypatch):
    def _raise(*a, **k):
        raise OSError("연결 실패")

    monkeypatch.setattr(update_check.urllib.request, "urlopen", _raise)

    assert update_check.check_for_update() is None


def test_returns_none_on_empty_releases_list(monkeypatch):
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"[]"))

    assert update_check.check_for_update() is None


def test_returns_none_on_malformed_tag(monkeypatch):
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: _releases_response("nightly-build"))

    assert update_check.check_for_update() is None
