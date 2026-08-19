"""platform_info.py 단위 시험 — OS·Architecture 매핑, 미지원 값은 명확히 실패."""

import pytest

from slash_pc_runner import platform_info


@pytest.mark.parametrize(
    "system, expected",
    [("Windows", "WINDOWS"), ("Darwin", "MACOS")],
)
def test_detect_os_maps_known_platforms(monkeypatch, system, expected):
    monkeypatch.setattr(platform_info.platform, "system", lambda: system)

    assert platform_info.detect_os() == expected


def test_detect_os_rejects_unknown_platform(monkeypatch):
    monkeypatch.setattr(platform_info.platform, "system", lambda: "Linux")

    with pytest.raises(platform_info.UnsupportedPlatformError):
        platform_info.detect_os()


@pytest.mark.parametrize(
    "machine, expected",
    [
        ("arm64", "ARM64"),
        ("aarch64", "ARM64"),
        ("AMD64", "X86_64"),
        ("x86_64", "X86_64"),
    ],
)
def test_detect_architecture_maps_known_machines(monkeypatch, machine, expected):
    monkeypatch.setattr(platform_info.platform, "machine", lambda: machine)

    assert platform_info.detect_architecture() == expected


def test_detect_architecture_rejects_unknown_machine(monkeypatch):
    # 32비트 x86처럼 지원 목록에 없는 값은 조용히 X86_64로 대체하지 않는다.
    monkeypatch.setattr(platform_info.platform, "machine", lambda: "i386")

    with pytest.raises(platform_info.UnsupportedPlatformError):
        platform_info.detect_architecture()
