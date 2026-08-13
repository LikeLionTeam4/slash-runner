"""기기 식별 정보(개인키·deviceId·deviceToken) 영속화.

agentIdentityStore.ts + main.cjs의 createIdentityStore(safeStorage/Keychain) 대응.
keyring이 OS별 보안 저장소(macOS Keychain / Windows Credential Manager)를 알아서 골라 쓴다 —
Electron의 safeStorage와 동등한 신뢰 경계(같은 OS 사용자 계정 밖에서는 복호화 불가).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
