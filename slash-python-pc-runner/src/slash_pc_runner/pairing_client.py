"""HTTP 페어링/토큰 갱신 — pairingClient.ts 대응."""

from __future__ import annotations

import json
import platform
import urllib.error
import urllib.request
from typing import Optional

from ._build_info import get_agent_version


def _post_json(url: str, body: dict, headers: Optional[dict] = None) -> dict:
    """페어링 REST 응답은 {data,meta} 봉투를 쓴다 (메시지 프로토콜 문서 §3.3)."""
    data = json.dumps(body).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(req) as res:
            parsed = json.loads(res.read().decode("utf-8"))
            return parsed["data"]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8")
        try:
            parsed = json.loads(body_text)
            message = f"{parsed['error']['code']}: {parsed['error']['message']}"
        except (json.JSONDecodeError, KeyError):
            message = f"HTTP {e.code}"
        raise RuntimeError(f"POST {url} failed: {message}") from e


def _architecture() -> str:
    return "ARM64" if platform.machine() in ("arm64", "aarch64") else "X86_64"


def pair_agent(
    api_base_url: str,
    pairing_code: str,
    public_key_base64: str,
    device_name: str,
    supported_task_types: list[str],
) -> dict:
    body = {
        "pairingCode": pairing_code,
        "publicKey": public_key_base64,
        "device": {
            "name": device_name,
            "os": "MACOS",
            "architecture": _architecture(),
            "osVersion": platform.platform(),
            "agentVersion": get_agent_version(),
        },
        "supportedTaskTypes": supported_task_types,
    }
    return _post_json(f"{api_base_url}/api/v1/agent/pair", body)


def verify_pairing(api_base_url: str, pairing_session_id: str, challenge_id: str, signature: str) -> dict:
    body = {"pairingSessionId": pairing_session_id, "challengeId": challenge_id, "signature": signature}
    return _post_json(f"{api_base_url}/api/v1/agent/pair/verify", body)


def refresh_session(
    api_base_url: str,
    current_device_token: str,
    device_id: str,
    refresh_nonce: str,
    requested_at: str,
    signature: str,
) -> dict:
    """재페어링 없이 기기 인증 토큰만 갱신 (메시지 프로토콜 문서 §8.1 3단계)."""
    body = {"deviceId": device_id, "refreshNonce": refresh_nonce, "requestedAt": requested_at, "signature": signature}
    return _post_json(
        f"{api_base_url}/api/v1/agent/sessions/refresh",
        body,
        headers={"Authorization": f"Bearer {current_device_token}"},
    )
