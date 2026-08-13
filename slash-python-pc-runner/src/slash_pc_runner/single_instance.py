"""트레이 앱 중복 실행 방지.

Windows에서 Slash.exe를 여러 번 실행하면 트레이 아이콘이 그 개수만큼 뜨는 걸
직접 재현 확인했다 — 각 인스턴스가 같은(영속화된) 기기 식별정보로 mock-api에 독립적인
WSS 연결을 만들고, search-folders.json·processed-tasks.json 같은 공유 파일에 동시에
쓰기 경합을 일으킬 수 있는 잠재적 버그였다. 두 번째 이후 실행은 조용히 종료한다.

색인 폴더 관리 창(같은 실행 파일을 --folders-window 인자로 재사용 — __main__.py 참고)은
이 락과 무관하게 독립적으로 여러 번 떠야 하므로, 이 락은 트레이 진입점에서만 건다.
"""

from __future__ import annotations

import sys

_LOCK_NAME = "SlashTray"
_lock_handle = None  # 이 프로세스가 살아있는 동안 락을 계속 쥐고 있어야 해서 참조를 유지한다


def acquire() -> bool:
    """이 프로세스가 유일한 트레이 인스턴스면 True, 이미 다른 인스턴스가 떠 있으면 False."""
    global _lock_handle
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.CreateMutexW(None, False, f"Global\\{_LOCK_NAME}")
        already_running = ctypes.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        if already_running:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        _lock_handle = handle
        return True

    import fcntl

    from .resources import config_dir

    lock_path = config_dir() / f".{_LOCK_NAME}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _lock_handle = fh
    return True
