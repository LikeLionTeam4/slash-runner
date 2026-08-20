"""빌드 시점 커밋 SHA·날짜 — 배포된 실행 파일이 정확히 어느 커밋에서, 언제 나온 빌드인지
항상 구분하기 위한 것이다. 버전 번호(PACKAGE_VERSION)만으로는 부족하다 — 버전을 안 올린 채
여러 PR이 머지되면, 이미 배포된 실행 파일과 최신 dev 코드가 같은 번호를 달고도 서로 다른
상태가 된다(2026-08-18 slash-api#25 재검증 혼선이 실제 사례).

SHA만으로는 "정확히 어느 커밋인지"는 알아도 "그게 최신인지"는 알 수 없다 — api·nlu·llm은
ArgoCD가 항상 최신 상태를 강제해서 이 구분이 덜 급하지만(현재 배포 SHA는 values-dev.yaml
최신 커밋을 보면 안다), slash-runner는 다운로드된 실행 파일이 그대로 남는 배포 구조라
비교할 살아있는 기준점이 없다. 그래서 SHA에 날짜까지 같이 새겨서, git 로그를 따로 안 봐도
두 빌드를 나란히 놓고 바로 신구를 비교할 수 있게 한다(docker version의
Git commit/Built, kubectl version의 GitCommit/BuildDate와 같은 방식).

CI·패키징(.spec)이 빌드 직전에 ``_build_sha.txt``·``_build_date.txt``(둘 다 gitignored)를
채워 두면 PyInstaller가 데이터로 함께 얼린다. 소스에서 바로 실행할 때(개발 모드)는 그
파일들이 없으므로 git으로 직접 조회한 값을 그 자리에서 쓴다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .resources import resource_path

# pyproject.toml의 version과 동기화해서 유지한다 — 버전을 올릴 때 여기도 같이 바꾼다.
PACKAGE_VERSION = "0.4.2"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_bundled_or_git(bundled_filename: str, git_args: list[str]) -> str:
    bundled = resource_path(bundled_filename)
    if bundled.exists():
        content = bundled.read_text(encoding="utf-8").strip()
        if content:
            return content
    try:
        result = subprocess.run(
            git_args,
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def get_build_sha() -> str:
    return _read_bundled_or_git("_build_sha.txt", ["git", "rev-parse", "--short", "HEAD"])


def get_build_date() -> str:
    """커밋 날짜(YYYYMMDD) — 실제 패키징 시각이 아니라 그 커밋이 만들어진 시점을 쓴다.
    빌드가 늦게 돌아도(CI 재시도 등) 항상 코드 기준 시점을 가리키도록."""
    return _read_bundled_or_git(
        "_build_date.txt", ["git", "log", "-1", "--format=%cd", "--date=format:%Y%m%d"]
    )


def get_agent_version() -> str:
    """HELLO·페어링에 실어 보내는 agentVersion 값. semver build metadata 표기를 따라
    ``버전+커밋SHA.빌드일자`` 형태로 만든다 — 값 하나만 보면 정확히 어느 커밋의, 언제
    나온 빌드인지 바로 알 수 있다."""
    return f"slash-pc-runner-py/{PACKAGE_VERSION}+{get_build_sha()[:7]}.{get_build_date()}"
