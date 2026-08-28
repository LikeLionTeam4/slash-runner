"""기기 식별 정보(개인키·deviceId·deviceToken) 영속화.

agentIdentityStore.ts + main.cjs의 createIdentityStore(safeStorage/Keychain) 대응
(Electron/TypeScript 시절 원본, 저장소 Python 재작성으로 소멸).

원래는 keyring(OS 보안 저장소 — macOS Keychain / Windows Credential Manager)을 썼는데,
ad-hoc 서명 macOS 앱은 빌드마다 CDHash가 달라져 Keychain 접근 권한(ACL)이 앱 바이너리
단위로 깨진다 — 재빌드·업데이트할 때마다 페어링을 다시 해야 하는 문제로 실측 확인했다.
의도한 보안 경계는 원래 "같은 OS 사용자 계정 밖에서는 복호화 불가"였지 "같은 바이너리
서명이어야 함"이 아니었다(Electron 시절 safeStorage와 맞추려던 것뿐). 그래서
FileIdentityStore로 옮긴다 — config_dir() 아래 파일에 저장하고 권한(0600, POSIX)으로
같은 경계를 지킨다. SSH 키(~/.ssh/id_ed25519)·AWS/gcloud CLI 자격 증명과 같은 패턴이다.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import keyring
import keyring.errors


@dataclass
class PersistedAgentIdentity:
    device_id: str
    device_token: str
    private_key_pem: str
    public_key_base64: str

    def to_dict(self) -> dict:
        return {
            "deviceId": self.device_id,
            "deviceToken": self.device_token,
            "privateKeyPem": self.private_key_pem,
            "publicKeyBase64": self.public_key_base64,
        }

    @staticmethod
    def from_dict(data: dict) -> "PersistedAgentIdentity":
        return PersistedAgentIdentity(
            device_id=data["deviceId"],
            device_token=data["deviceToken"],
            private_key_pem=data["privateKeyPem"],
            public_key_base64=data["publicKeyBase64"],
        )


class AgentIdentityStore(Protocol):
    def load(self) -> Optional[PersistedAgentIdentity]: ...
    def save(self, identity: PersistedAgentIdentity) -> None: ...
    def clear(self) -> None: ...


class KeyringIdentityStore:
    """서비스명·계정명 조합으로 OS 보안 저장소에 식별정보 JSON을 통째로 저장."""

    def __init__(self, service_name: str = "slash-pc-runner", account: str = "device-identity"):
        self._service_name = service_name
        self._account = account

    def load(self) -> Optional[PersistedAgentIdentity]:
        raw = keyring.get_password(self._service_name, self._account)
        if raw is None:
            return None
        try:
            return PersistedAgentIdentity.from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError):
            return None

    def save(self, identity: PersistedAgentIdentity) -> None:
        keyring.set_password(self._service_name, self._account, json.dumps(identity.to_dict()))

    def clear(self) -> None:
        try:
            keyring.delete_password(self._service_name, self._account)
        except keyring.errors.PasswordDeleteError:
            pass


class FileIdentityStore:
    """config_dir() 아래 파일 하나에 식별정보 JSON을 저장. POSIX(macOS)는 저장 직후
    0600(소유자만 읽기·쓰기)으로 권한을 좁힌다 — Windows는 %APPDATA%가 이미 그 사용자
    계정 전용 ACL이라 별도 처리가 없어도 같은 경계를 만족한다.

    legacy_keyring_store를 넘기면, 이 파일이 아직 없을 때 예전 KeyringIdentityStore도
    한 번 확인한다 — 있으면 파일로 옮겨 쓰고 Keychain 쪽은 지운다. 이전에 이미 페어링된
    사용자가 이번 전환 때문에 재등록을 겪지 않도록 하기 위한 1회성 마이그레이션이다.
    """

    def __init__(self, path: Path, legacy_keyring_store: Optional["KeyringIdentityStore"] = None):
        self._path = path
        self._legacy_keyring_store = legacy_keyring_store

    def load(self) -> Optional[PersistedAgentIdentity]:
        identity = self._load_from_file()
        if identity is not None:
            return identity
        return self._migrate_from_keyring()

    def _load_from_file(self) -> Optional[PersistedAgentIdentity]:
        if not self._path.exists():
            return None
        try:
            return PersistedAgentIdentity.from_dict(json.loads(self._path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _migrate_from_keyring(self) -> Optional[PersistedAgentIdentity]:
        if self._legacy_keyring_store is None:
            return None
        identity = self._legacy_keyring_store.load()
        if identity is None:
            return None
        # 새 파일에 쓰다가 실패하면(디스크 오류 등) 예전 Keychain 값은 그대로 남겨둔다 —
        # save()가 예외 없이 끝난 뒤에만 Keychain을 지운다.
        self.save(identity)
        self._legacy_keyring_store.clear()
        return identity

    def save(self, identity: PersistedAgentIdentity) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(identity.to_dict()), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
