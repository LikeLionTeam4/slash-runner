"""실행 중인 PC의 OS·Architecture를 감지한다.

Pairing 요청(pairing_client.py)과 WSS HELLO(agent.py)가 반드시 같은 함수를 써야 두 값이
어긋나지 않는다. 예전엔 각자 ``os="MACOS"``를 박아 두고 architecture 계산도 따로
복붙해서, Windows PC도 자신을 macOS로 서버에 등록하는 결함이 있었다 — 서버의
`DeviceOs` enum에 MACOS·WINDOWS 둘 다 있어 값 자체가 거부되지 않다 보니, 조용히 잘못
저장되고 아무도 알아채지 못했다.

지원하지 않는 OS·Architecture는 조용히 대체값(예: MACOS)으로 등록하지 않고 명확히
실패한다 — 잘못된 값으로 등록되면 서버·화면 어디서도 그 뒤에 알아챌 방법이 없다.
"""

from __future__ import annotations

import platform


class UnsupportedPlatformError(RuntimeError):
    """이 PC의 OS 또는 Architecture를 서버 계약(DeviceOs·DeviceArchitecture)으로 매핑할 수 없다."""


def detect_os() -> str:
    system = platform.system()
    if system == "Windows":
        return "WINDOWS"
    if system == "Darwin":
        return "MACOS"
    raise UnsupportedPlatformError(f"지원하지 않는 운영체제입니다: {system!r}")


def detect_architecture() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "ARM64"
    if machine in ("amd64", "x86_64"):
        return "X86_64"
    raise UnsupportedPlatformError(f"지원하지 않는 CPU 아키텍처입니다: {machine!r}")
