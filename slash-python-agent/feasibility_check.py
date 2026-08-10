"""
PyInstaller 패키징 실현 가능성 검증용 스파이크.

현재 Electron 버전(agent-app)이 실제로 쓰는 세 가지 핵심 기능이 PyInstaller로 얼린
바이너리 안에서도 그대로 동작하는지 확인한다:
  1. keyring — macOS Keychain 읽기/쓰기 (safeStorage 대응)
  2. sqlite3 FTS5 — 파일 색인 (SQLite FTS5 재설계 대응)
  3. rumps — 메뉴바 트레이 아이콘

결과는 사람이 클릭하지 않아도 확인 가능하도록 로그 파일에 남긴다.
"""

import sqlite3
import sys
import time
from pathlib import Path

LOG_PATH = Path.home() / "slash-python-agent-results.log"


def log(line: str) -> None:
    with LOG_PATH.open("a") as f:
        f.write(f"{line}\n")
    print(line)


def check_keyring() -> bool:
    try:
        import keyring

        service = "slash-python-agent"
        account = "test-account"
        value = "test-secret-abc123"
        keyring.set_password(service, account, value)
        read_back = keyring.get_password(service, account)
        keyring.delete_password(service, account)
        ok = read_back == value
        log(f"[keyring] {'PASS' if ok else 'FAIL'} — wrote/read/deleted Keychain 항목, backend={keyring.get_keyring()}")
        return ok
    except Exception as e:
        log(f"[keyring] FAIL — {type(e).__name__}: {e}")
        return False


def check_sqlite_fts5() -> bool:
    try:
        db_path = Path.home() / "slash-python-agent-fts5-test.db"
        if db_path.exists():
            db_path.unlink()
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE VIRTUAL TABLE file_entries_fts USING fts5(name, relative_path)")
        conn.executemany(
            "INSERT INTO file_entries_fts (name, relative_path) VALUES (?, ?)",
            [
                ("보고서_최종.docx", "documents/보고서_최종.docx"),
                ("budget.xlsx", "finance/budget.xlsx"),
                ("README.md", "project-x/README.md"),
            ],
        )
        conn.commit()
        rows = conn.execute(
            "SELECT name FROM file_entries_fts WHERE file_entries_fts MATCH ?", ("보고서",)
        ).fetchall()
        conn.close()
        db_path.unlink()
        ok = len(rows) == 1 and rows[0][0] == "보고서_최종.docx"
        log(f"[sqlite3 FTS5] {'PASS' if ok else 'FAIL'} — 검색 결과: {rows}")
        return ok
    except Exception as e:
        log(f"[sqlite3 FTS5] FAIL — {type(e).__name__}: {e}")
        return False


def main() -> None:
    LOG_PATH.write_text("")  # 이전 결과 초기화
    log(f"=== 검증 시작 (frozen={getattr(sys, 'frozen', False)}) ===")

    keyring_ok = check_keyring()
    sqlite_ok = check_sqlite_fts5()

    log(f"=== 결과 요약: keyring={keyring_ok}, sqlite_fts5={sqlite_ok} ===")

    try:
        import rumps

        log("[rumps] 트레이 아이콘 기동 시도")

        class TrayApp(rumps.App):
            def __init__(self):
                super().__init__("SlashPyAgent", quit_button="종료")

            @rumps.timer(3)
            def heartbeat(self, _sender):
                log(f"[rumps] heartbeat tick at {time.time()}")

        log("[rumps] PASS — App 인스턴스 생성 성공, run() 진입")
        TrayApp().run()
    except Exception as e:
        log(f"[rumps] FAIL — {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
