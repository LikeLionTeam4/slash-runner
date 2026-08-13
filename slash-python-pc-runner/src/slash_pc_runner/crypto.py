"""Ed25519 기기 신원 — agentCrypto.ts 대응."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@dataclass
class AgentKeyPair:
    private_key: Ed25519PrivateKey
    # raw 32byte를 표준 base64로 인코딩한 값 — 서버 ed25519.ts와 대칭
    public_key_base64: str

    def sign(self, payload: str) -> str:
        signature = self.private_key.sign(payload.encode("utf-8"))
        return base64.b64encode(signature).decode("ascii")

    def export_private_key_pem(self) -> str:
        """영속 저장용 PKCS8 PEM 직렬화."""
        pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return pem.decode("ascii")


def generate_agent_key_pair() -> AgentKeyPair:
    private_key = Ed25519PrivateKey.generate()
    raw_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return AgentKeyPair(private_key=private_key, public_key_base64=base64.b64encode(raw_public).decode("ascii"))


def restore_agent_key_pair(private_key_pem: str, public_key_base64: str) -> AgentKeyPair:
    """저장소에서 불러온 PEM + publicKeyBase64로 키쌍을 복원(재페어링 없이 재시작하기 위함)."""
    private_key = serialization.load_pem_private_key(private_key_pem.encode("ascii"), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("private_key_pem은 Ed25519 개인키여야 합니다")
    return AgentKeyPair(private_key=private_key, public_key_base64=public_key_base64)
