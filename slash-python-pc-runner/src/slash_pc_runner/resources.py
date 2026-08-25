"""번들 리소스(아이콘·HTML·기본 색인 폴더) 경로 해석 — 개발 모드와 PyInstaller로 얼린
실행 파일 모드 둘 다에서 같은 방식으로 쓰기 위한 공통 헬퍼.

PyInstaller onefile 빌드는 실행 시점에 번들된 데이터 파일을 임시 디렉터리(sys._MEIPASS)에
풀어놓는다 — 얼린 상태에서는 __file__ 기준 상대경로 대신 그쪽을 봐야 한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import certifi


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def configure_ssl_certificates() -> None:
    """`urllib`·`websockets` 등이 기본으로 쓰는 `ssl.create_default_context()`가 인증서
    체인을 검증할 수 있게 한다.

    개발 모드는 시스템 Python(Homebrew 등)이 OS 인증서 저장소를 찾아 문제가 없지만,
    PyInstaller로 얼린 실행 파일은 완전히 격리된 Python이라 그 경로에 의존할 수 없고
    인증서 자체가 번들에 없다 — HTTPS/WSS 요청이 전부 `CERTIFICATE_VERIFY_FAILED`로
    실패한다. `SSL_CERT_FILE`을 지정하면 OpenSSL이 기본 경로 대신 이 파일을 쓴다.
    이미 설정돼 있으면(예: 사내 프록시 CA) 덮어쓰지 않는다."""
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def resolve_cli_path() -> None:
    """`claude`/`codex` CLI가 설치돼 있어도 GUI로 실행된 얼린 앱이 못 찾는 문제를 고친다
    (`slash-runner#44`).

    macOS에서 Finder로 실행한 GUI 프로세스는 `launchd`의 기본 PATH
    (`/usr/bin:/bin:/usr/sbin:/sbin`)만 물려받고, 터미널 셸(`~/.zshrc` 등)이 추가하는
    Homebrew 등의 경로는 없다 — 실제로 실행 중인 `Slash.app`의 PATH를 `ps eww`로 직접
    조회해 확인된 사실이다. `code_adapters.py`의 `claude_code_available()`/
    `codex_available()`과 그 뒤의 `subprocess` 호출은 전부 `shutil.which`·`os.environ`
    기본 동작에 의존하므로, 여기서 프로세스 시작 시 한 번만 PATH를 보강해 두면 호출부를
    바꿀 필요가 없다.

    로그인 셸을 1회 실행해 그 PATH를 뒤에 이어 붙인다. 개발 모드(터미널에서 실행)는 이미
    올바른 PATH를 상속하므로 사실상 아무것도 안 바뀐다. 셸 조회가 실패해도(Windows는 이
    문제 자체가 없어 애초에 시도하지 않음, 그 외 실패는 타임아웃·예외 전부 포함) 기존
    PATH를 그대로 둔다 — 상황을 악화시키지 않는다."""
    if sys.platform == "win32":
        return
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        result = subprocess.run(
            [shell, "-l", "-c", 'echo -n "$PATH"'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return
    shell_path = result.stdout.strip()
    if not shell_path:
        return

    current = os.environ.get("PATH", "")
    existing = set(current.split(os.pathsep)) if current else set()
    additions = [p for p in shell_path.split(os.pathsep) if p and p not in existing]
    if not additions:
        return
    os.environ["PATH"] = os.pathsep.join([current, *additions]) if current else os.pathsep.join(additions)


def unblock_own_files() -> None:
    """Windows — 다운로드한 zip을 풀어 실행하면 이 앱의 모든 파일에 "인터넷에서 받음"
    표시(NTFS `Zone.Identifier` 대체 데이터 스트림, MOTW)가 남는다. .NET Framework의
    netfx 로더가 이 표시가 있는 위치의 어셈블리를 신뢰하지 않는 존으로 취급해 pythonnet
    로딩이 "Failed to resolve Python.Runtime.Loader.Initialize"로 실패하는 원인 중
    하나였다(실측 확인, 2026-08-25 — `Get-ChildItem -Recurse | Unblock-File`로 수동
    해제하자 이 오류가 사라졌다). macOS의 `xattr -cr`(Gatekeeper 격리 해제)과 같은
    목적이지만, 그쪽은 앱 밖의 사용자 조치가 필요한 반면 Windows는 앱 자신의 파일이라
    시작 시 스스로 해제할 수 있다 — 사용자에게 별도 안내 없이 고칠 수 있는 부분은
    코드로 고친다.

    실패해도(권한 문제·스트림이 이미 없음 등) 조용히 넘어간다 — 이미 해제돼 있으면
    지울 스트림 자체가 없는 게 정상이라 대부분의 파일에서 예외가 나는 게 정상 동작이다."""
    if sys.platform != "win32" or not is_frozen():
        return
    app_dir = Path(sys.executable).resolve().parent
    for path in app_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            os.remove(f"{path}:Zone.Identifier")
        except OSError:
            pass


def config_dir() -> Path:
    """앱 설정·상태 저장 디렉터리 — OS별 관례 경로로 분기한다.
    Windows는 %APPDATA%(없으면 홈 폴더 밑 AppData\\Roaming으로 폴백)에
    slash-pc-runner-py를 쓴다. macOS는 Application Support 아래 폴더명을 제품명
    그대로 "slash"로 둔다 — Finder에서 사용자에게 그대로 보이는 이름이라, 패키지명보다
    실제 앱 이름(Slash)에 가까운 쪽이 낫다는 판단이다."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "slash-pc-runner-py"
    return Path.home() / "Library" / "Application Support" / "slash"


def migrate_legacy_config_dir() -> None:
    """이전 폴더명(slash-pc-runner-py)으로 macOS에 이미 저장돼 있던 설정·페어링 정보를
    새 폴더명(slash)으로 옮긴다. config_dir()를 쓰는 어떤 코드보다도 먼저, 앱 시작
    시점에 한 번만 호출해야 한다 — 새 폴더가 먼저 만들어지면(예: 락 파일) 아래
    "새 폴더가 아직 없다" 조건이 깨져 마이그레이션을 건너뛴다.

    Windows는 폴더명을 안 바꿨으니 옮길 게 없다."""
    if sys.platform == "win32":
        return
    old_dir = Path.home() / "Library" / "Application Support" / "slash-pc-runner-py"
    new_dir = config_dir()
    if old_dir.exists() and not new_dir.exists():
        old_dir.rename(new_dir)


def resource_path(*parts: str) -> Path:
    """개발 모드: 이 패키지(src/slash_pc_runner/) 기준. 얼린 모드: PyInstaller가 풀어둔 번들 루트 기준."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS"))
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def repo_fixtures_search_folder() -> Path:
    """개발 모드 기본 시드 폴더 — 이 저장소의 fixtures/search-folder.
    얼린 모드에서는 .spec이 번들 루트의 fixtures/search-folder로 같이 복사해 둔다."""
    if is_frozen():
        return resource_path("fixtures", "search-folder")
    return Path(__file__).resolve().parents[3] / "fixtures" / "search-folder"
