"""GitHub Releases 기준 최신 버전 확인 — 앱 시작 시 1회, 실패해도 조용히 넘어간다.

PyUpdater·Sparkle 같은 전용 업데이트 프레임워크 대신 표준 라이브러리만으로 최소
구현한다 — 확인 주기가 "시작 시 1회"뿐이라 백그라운드 상주 감시가 필요 없고, 새
의존성을 늘릴 이유가 없다.

이 저장소의 릴리스는 지금까지 전부 ``prerelease: true``(버전명에 "-pre"가 붙는
팀 컨벤션, 정식 배포 전이라는 뜻)라 GitHub API의 ``/releases/latest``는 쓸 수
없다 — 그 엔드포인트는 프리릴리스를 제외하고 찾아서, 전부 프리릴리스인 지금은
항상 404가 난다(실측 확인). 대신 ``/releases`` 목록을 받아 맨 앞(최신 생성 순)을
쓴다.

버전 비교는 SHA·날짜가 아니라 ``major.minor.patch``만 본다 — 이미 다운로드해서
쓰는 사람에게 의미 있는 신호는 "다음 배포판이 나왔는가"이지, dev의 개별 커밋이
아니다(그 용도는 agentVersion의 SHA+날짜가 이미 맡고 있다, `_build_info.py` 참고).
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Optional

from ._build_info import PACKAGE_VERSION

_RELEASES_URL = "https://api.github.com/repos/LikeLionTeam4/slash-runner/releases"
_TIMEOUT_SECONDS = 3.0
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class UpdateCheckResult:
    update_available: bool
    latest_version: str
    release_url: str


def _parse_version(text: str) -> Optional[tuple[int, int, int]]:
    match = _VERSION_PATTERN.match(text)
    if not match:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def check_for_update() -> Optional[UpdateCheckResult]:
    """실패(네트워크·rate limit·형식 오류)하면 None을 돌려준다.

    자동 검사는 있으면 좋은 부가 기능이지 필수 경로가 아니므로, 어떤 이유로든
    실패하면 예외를 던지지 않고 조용히 포기한다(_build_info.py의 unknown 반환과
    같은 태도 — 실패가 곧 "확인 못 함"이지 "에러"가 아니다).
    """
    try:
        request = urllib.request.Request(_RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            releases = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    if not releases:
        return None

    latest_tag = releases[0].get("tag_name", "")
    latest_version = _parse_version(latest_tag)
    current_version = _parse_version(PACKAGE_VERSION)
    if latest_version is None or current_version is None:
        return None

    return UpdateCheckResult(
        update_available=latest_version > current_version,
        latest_version=latest_tag,
        release_url=releases[0].get("html_url", ""),
    )
