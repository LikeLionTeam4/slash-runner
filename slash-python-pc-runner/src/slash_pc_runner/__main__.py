"""단일 진입점 — `python -m slash_pc_runner`(개발) / PyInstaller가 얼리는 실행 파일(배포) 둘 다 여기로 들어온다.

얼린 실행 파일은 엔트리 스크립트가 하나뿐이라, 트레이 앱 자신이 "색인 폴더 관리" 창을 별도
프로세스로 띄울 때도 같은 실행 파일을 인자만 다르게 줘서 재사용해야 한다(tray_app.py의
_folders_window_command 참고) — 그래서 트레이 기동과 색인 폴더 창 둘 다 이 파일 하나가
분기해서 처리한다.
"""

from __future__ import annotations

import os
import sys

# pywebview의 winforms 백엔드(Windows GUI, 트레이·페어링창·폴더창 전부 이걸 거친다)가
# 내부적으로 pythonnet(import clr)을 쓴다. pythonnet은 Windows에서 기본값으로 구식 netfx
# (.NET Framework) 경로를 쓰는데, 이 경로가 PyInstaller로 얼린 실행 파일 환경에서
# "Failed to resolve Python.Runtime.Loader.Initialize"로 실패하는 걸 실기기로 확인했다
# (2026-08-25). pywebview 자신도 netfx import 실패 시 coreclr로 재시도하는 폴백이 있지만
# (webview/platforms/winforms.py의 try/except), 실패한 첫 시도가 같은 프로세스의 CLR 호스트
# 상태를 오염시켜 재시도가 항상 깨끗이 성공한다는 보장이 없다 — 그래서 아예 처음부터
# coreclr을 쓰도록 강제한다. 이 파일이 로드되는 즉시 적용돼야(어떤 분기를 타든 무조건)
# 하므로 함수 안이 아니라 모듈 최상단에 둔다. macOS·Linux는 pythonnet/clr을 아예 안 쓰므로
# 이 값이 있어도 무해하다.
#
# 미검증: 이 PC에 .NET Desktop Runtime(Microsoft.WindowsDesktop.App)이 없으면 coreclr을
# 강제해도 System.Windows.Forms 로딩이 별도로 실패할 수 있다 — 아직 실기기로 끝까지
# 검증되지 않았다(not_push_git_docs의 관련 지시문 참고).
os.environ.setdefault("PYTHONNET_RUNTIME", "coreclr")


def main() -> None:
    # 다른 어떤 분기보다 먼저 — single_instance.py의 락 파일 생성을 포함해 config_dir()를
    # 쓰는 모든 코드보다 앞서야 마이그레이션이 "새 폴더가 아직 없다" 조건을 안전하게 본다
    # (resources.migrate_legacy_config_dir() 주석 참고).
    from .resources import migrate_legacy_config_dir, resolve_cli_path, unblock_own_files

    # webview/pythonnet을 쓰는 어떤 분기보다 먼저 — Windows에서 다운로드한 zip의 MOTW
    # 표시가 pythonnet 로딩 실패의 원인이 될 수 있다(위 PYTHONNET_RUNTIME 주석 참고).
    unblock_own_files()
    migrate_legacy_config_dir()
    # claude/codex CLI를 실제로 실행하는 분기(트레이/에이전트)보다 먼저 — GUI로 실행된
    # 얼린 앱은 launchd 기본 PATH만 가져 Homebrew 등에 설치된 CLI를 못 찾는다(#44).
    resolve_cli_path()

    if len(sys.argv) >= 3 and sys.argv[1] == "--folders-window":
        from .folders_window import run

        # run()은 반환하지 않는다 — Api.save/cancel이나 창 닫기 핸들러가 os._exit()로 이미
        # 프로세스를 끝낸다(folders_window.py 주석 참고). 여기 도달하면 이례적인 경우다.
        run(sys.argv[2])
        sys.exit(1)

    if len(sys.argv) >= 3 and sys.argv[1] == "--project-workspaces-window":
        from .project_workspaces_window import run

        # run()은 반환하지 않는다 — Api.save/cancel이나 창 닫기 핸들러가 os._exit()로
        # 프로세스를 직접 끝낸다(project_workspaces_window.py 주석 참고).
        run(sys.argv[2])
        sys.exit(1)

    if len(sys.argv) >= 2 and sys.argv[1] == "--pairing-window":
        from .pairing_window import main as pairing_main

        sys.argv = sys.argv[1:]  # pairing_window.main()이 sys.argv[1]을 오류 메시지로 읽는다
        pairing_main()
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "--ssl-selfcheck":
        # 얼린 실행 파일에 인증서 번들이 실제로 딸려 왔는지 CI/로컬에서 GUI 없이 확인하는
        # 자리. urllib 기본 컨텍스트가 인증서를 못 찾으면(패키징 결함) 예외로 즉시 드러난다
        # — resources.configure_ssl_certificates()가 없던 시절 실제로 겪은 결함이다.
        import urllib.request

        url = sys.argv[2] if len(sys.argv) >= 3 else "https://api.dev.sbsh.cloud/api/v1/health/dependencies"
        try:
            with urllib.request.urlopen(url, timeout=10) as res:
                print(f"SSL_SELFCHECK_OK status={res.status}")
        except Exception as e:
            print(f"SSL_SELFCHECK_FAIL {e}")
            sys.exit(1)
        return

    from .tray_app import main as tray_main

    tray_main()


if __name__ == "__main__":
    main()
