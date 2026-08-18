# PyInstaller 스펙 — 트레이 앱(tray_app.py)과 색인 폴더 관리 창(folders_window.py)을
# 하나의 실행 파일로 묶는다(단일 진입점은 __main__.py, --folders-window 인자로 분기).
#
# 빌드: cd slash-python-pc-runner && pyinstaller slash_pc_runner.spec

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)
repo_root = project_root.parent
package_dir = project_root / "src" / "slash_pc_runner"

datas = [
    (str(package_dir / "folders_window.html"), "."),
    (str(package_dir / "assets"), "assets"),
    (str(repo_root / "fixtures" / "search-folder"), "fixtures/search-folder"),
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
        "CFBundleShortVersionString": "0.4.0",
        # 메뉴바 전용 앱 — Dock 아이콘/앱 전환기에 안 뜬다(Electron agent-app의
        # LSUIElement와 동일 설정). 이게 걸리면 tray_app.py/folders_window.py의
        # 런타임 NSApplicationActivationPolicyAccessory 호출은 사실상 불필요해지지만,
        # 개발 모드(python -m ...)에서도 똑같이 동작해야 하니 코드 쪽엔 남겨둔다.
        "LSUIElement": True,
    },
)
