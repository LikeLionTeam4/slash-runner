# PyInstaller 스펙(Windows) — macOS용 slash_pc_runner.spec과 같은 구조(단일 진입점 run.py,
# 트레이+색인 폴더 창을 인자로 분기)를 Windows에 맞게 옮긴 것.
#
# 주의: 이 스펙은 macOS 개발 환경에서 "작성"만 했다 — PyInstaller는 크로스 컴파일을 지원하지
# 않아 실제 빌드·실행 검증은 Windows 머신에서 직접 해야 한다(아직 못함, 알려진 한계).
#
# 빌드(Windows에서): cd slash-python-pc-runner && pyinstaller slash_pc_runner_windows.spec

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
    # (macOS용 spec의 AppKit/Foundation/objc/cocoa와 같은 이유) — Windows 쪽 백엔드로 대응.
    hiddenimports=[
        "pystray._win32",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "webview.platforms.mshtml",
        "clr",
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
    # 콘솔 창을 띄우지 않는다 — 트레이 전용 백그라운드 앱(macOS EXE의 console=False와 동일 목적).
    console=False,
    icon=str(package_dir / "assets" / "AppIcon.ico"),
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
