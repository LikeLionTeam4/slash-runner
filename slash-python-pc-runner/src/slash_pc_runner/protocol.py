"""Agent WSS 메시지 프로토콜 상수·헬퍼 — contracts/src/agentMessages.ts·enums.ts 대응."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

AGENT_SCHEMA_VERSION = "1.0"

TASK_TYPES = ("WEATHER_LOOKUP", "FILE_SEARCH", "SYSTEM_STATUS", "TEXT_SUMMARY", "CODE_ANALYSIS")

REASON_CODES = (
    "DEVICE_BUSY",
    "TASK_TYPE_NOT_SUPPORTED",
    "INVALID_PARAMETERS",
    "SEARCH_FOLDER_NOT_FOUND",
    "WORKSPACE_NOT_FOUND",
    "CODE_AGENT_NOT_CONFIGURED",
    "TASK_EXPIRED",
    "POLICY_DENIED",
)

PROTOCOL_ERROR_CODES = (
    "UNSUPPORTED_SCHEMA_VERSION",
    "INVALID_MESSAGE",
    "AUTHENTICATION_FAILED",
    "CHALLENGE_EXPIRED",
    "CHALLENGE_REUSED",
    "INVALID_CONNECTION_STATE",
    "DEVICE_REVOKED",
    "AGENT_VERSION_UNSUPPORTED",
    "PLATFORM_UNSUPPORTED",
)

# 지시문 §11 / 메시지 스펙 §6.2 — indexStatus와 동일한 3가지 값
INDEX_STATUSES = ("INDEXED", "INDEXING", "UNAVAILABLE")

KST = timezone(timedelta(hours=9))


def now_iso_kst() -> str:
    return datetime.now(KST).isoformat(timespec="milliseconds")


def to_iso_kst(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).isoformat(timespec="milliseconds")


def envelope(message_type: str, **fields: Any) -> dict:
    return {
        "schemaVersion": AGENT_SCHEMA_VERSION,
        "type": message_type,
        "eventId": str(uuid.uuid4()),
        "sentAt": now_iso_kst(),
        **fields,
    }


def build_challenge_signing_payload(challenge_id: str, nonce: str, device_id: str) -> str:
    return f"{challenge_id}:{nonce}:{device_id}"


def build_refresh_signing_payload(device_id: str, refresh_nonce: str, requested_at: str) -> str:
    return f"{device_id}:{refresh_nonce}:{requested_at}"
