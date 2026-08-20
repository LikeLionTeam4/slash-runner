"""identity_store.py의 FileIdentityStore 시험.

Keychain(ad-hoc 서명 CDHash 문제로 재빌드·업데이트마다 접근이 깨지던 것)을 대체하는
저장소라, 저장·조회 round-trip과 파일 권한뿐 아니라 예전 KeyringIdentityStore에서
자동으로 옮겨오는 마이그레이션 경로(기존 페어링 사용자가 재등록을 안 겪게 하는 목적)가
핵심이다.
"""

from __future__ import annotations

import stat
import sys

import pytest

from slash_pc_runner.identity_store import FileIdentityStore, PersistedAgentIdentity


def make_identity(suffix: str = "1") -> PersistedAgentIdentity:
    return PersistedAgentIdentity(
        device_id=f"device-{suffix}",
        device_token=f"token-{suffix}",
        private_key_pem=f"-----BEGIN PRIVATE KEY-----\n{suffix}\n-----END PRIVATE KEY-----",
        public_key_base64=f"pubkey-{suffix}",
    )


class FakeLegacyStore:
    """KeyringIdentityStore 대역 — 실제 Keychain을 안 건드리고 마이그레이션 경로만 검증."""

    def __init__(self, identity: PersistedAgentIdentity | None = None):
        self.identity = identity
        self.cleared = False

    def load(self):
        return self.identity

    def clear(self):
        self.cleared = True
        self.identity = None


class TestSaveAndLoad:
    def test_round_trips(self, tmp_path):
        store = FileIdentityStore(tmp_path / "device-identity.json")
        identity = make_identity()

        store.save(identity)

        assert store.load() == identity

    def test_load_returns_none_when_file_missing(self, tmp_path):
        store = FileIdentityStore(tmp_path / "device-identity.json")

        assert store.load() is None

    def test_clear_removes_file(self, tmp_path):
        path = tmp_path / "device-identity.json"
        store = FileIdentityStore(path)
        store.save(make_identity())

        store.clear()

        assert not path.exists()
        assert store.load() is None

    def test_clear_is_safe_when_file_missing(self, tmp_path):
        store = FileIdentityStore(tmp_path / "device-identity.json")

        store.clear()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX 파일 권한 전용")
    def test_save_restricts_permissions_on_posix(self, tmp_path):
        path = tmp_path / "device-identity.json"
        store = FileIdentityStore(path)

        store.save(make_identity())

        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == stat.S_IRUSR | stat.S_IWUSR


class TestKeyringMigration:
    """예전 Keychain에 저장돼 있던 사용자가 파일 저장으로 자동 전환되는지 — 이 전환이
    없으면 기존 사용자 전부가 이번 변경 한 번으로 강제 재등록을 겪는다."""

    def test_migrates_from_legacy_store_when_file_missing(self, tmp_path):
        legacy_identity = make_identity("legacy")
        legacy_store = FakeLegacyStore(legacy_identity)
        path = tmp_path / "device-identity.json"
        store = FileIdentityStore(path, legacy_keyring_store=legacy_store)

        loaded = store.load()

        assert loaded == legacy_identity
        assert path.exists()

    def test_clears_legacy_store_after_successful_migration(self, tmp_path):
        legacy_store = FakeLegacyStore(make_identity("legacy"))
        store = FileIdentityStore(tmp_path / "device-identity.json", legacy_keyring_store=legacy_store)

        store.load()

        assert legacy_store.cleared is True

    def test_does_not_touch_legacy_store_when_file_already_exists(self, tmp_path):
        path = tmp_path / "device-identity.json"
        FileIdentityStore(path).save(make_identity("current"))

        legacy_store = FakeLegacyStore(make_identity("legacy"))
        store = FileIdentityStore(path, legacy_keyring_store=legacy_store)

        loaded = store.load()

        assert loaded == make_identity("current")
        assert legacy_store.cleared is False

    def test_returns_none_when_neither_file_nor_legacy_has_identity(self, tmp_path):
        legacy_store = FakeLegacyStore(None)
        store = FileIdentityStore(tmp_path / "device-identity.json", legacy_keyring_store=legacy_store)

        assert store.load() is None

    def test_returns_none_when_no_legacy_store_given(self, tmp_path):
        store = FileIdentityStore(tmp_path / "device-identity.json")

        assert store.load() is None
