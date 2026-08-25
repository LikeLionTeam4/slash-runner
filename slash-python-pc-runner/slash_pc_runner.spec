# PyInstaller 스펙 — 트레이 앱(tray_app.py)과 색인 폴더 관리 창(folders_window.py)을
# 하나의 실행 파일로 묶는다(단일 진입점은 __main__.py, --folders-window 인자로 분기).
#
# 빌드: cd slash-python-pc-runner && pyinstaller slash_pc_runner.spec

import subprocess
import sys
from pathlib import Path

import certifi

block_cipher = None
project_root = Path(SPECPATH)
repo_root = project_root.parent
package_dir = project_root / "src" / "slash_pc_runner"

# 빌드 시점 커밋 SHA·날짜를 파일로 남겨서 함께 얼린다(_build_info.py가 런타임에 읽는다) —
# CI뿐 아니라 이 명령으로 직접 로컬에서 빌드해도 항상 정확한 값이 들어간다.
def _write_build_info() -> None:
    def _git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else ""

    (package_dir / "_build_sha.txt").write_text(_git("rev-parse", "--short", "HEAD"), encoding="utf-8")
    (package_dir / "_build_date.txt").write_text(
        _git("log", "-1", "--format=%cd", "--date=format:%Y%m%d"), encoding="utf-8"
    )


_write_build_info()

datas = [
    (str(package_dir / "folders_window.html"), "."),
    (str(package_dir / "pairing_window.html"), "."),
    (str(package_dir / "project_workspaces_window.html"), "."),
    (str(package_dir / "assets"), "assets"),
    (str(repo_root / "fixtures" / "search-folder"), "fixtures/search-folder"),
    (str(package_dir / "_build_sha.txt"), "."),
    (str(package_dir / "_build_date.txt"), "."),
    # certifi의 cacert.pem은 데이터 파일이라 PyInstaller가 .py처럼 자동으로 못 찾는다 —
    # 없으면 urllib·websockets의 기본 ssl 컨텍스트가 인증서 검증에 실패한다(resources.py
    # configure_ssl_certificates() 참고). "certifi" 목적지에 둬야 certifi.where()가
    # 얼린 상태에서도 os.path.dirname(__file__) 기준으로 같은 상대 위치를 찾는다.
    (certifi.where(), "certifi"),
]

a = Analysis(
    [str(project_root / "run.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    # pystray/pywebview는 실행 시점에 플랫폼별 백엔드를 골라 import해서 정적 분석에 안 잡힌다
    hiddenimports=[
        "webview.platforms.cocoa",
        "pystray._darwin",
        "AppKit",
        "Foundation",
        "objc",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Slash",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Slash",
)

app = BUNDLE(
    coll,
    name="Slash.app",
    icon=str(package_dir / "assets" / "AppIcon.icns"),
    bundle_identifier="com.slash-test.pc-runner",
    info_plist={
        "CFBundleName": "Slash",
        "CFBundleDisplayName": "Slash",
        "CFBundleShortVersionString": "0.5.6",
        # 메뉴바 전용 앱 — Dock 아이콘/앱 전환기에 안 뜬다(Electron agent-app의
        # LSUIElement와 동일 설정). 이게 걸리면 tray_app.py/folders_window.py의
        # 런타임 NSApplicationActivationPolicyAccessory 호출은 사실상 불필요해지지만,
        # 개발 모드(python -m ...)에서도 똑같이 동작해야 하니 코드 쪽엔 남겨둔다.
        "LSUIElement": True,
    },
)
