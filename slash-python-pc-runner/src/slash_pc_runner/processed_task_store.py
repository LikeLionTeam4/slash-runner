"""중복방지·재전송 이력(processed_tasks) 영속화 — processedTaskStore.ts 대응.

처리 이력은 자격증명이 아니라 서버에 이미 전송된 결과값이라 평문 JSON 파일로 충분하다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class ProcessedTaskStore(Protocol):
    def load(self) -> dict: ...
    def save(self, records: dict) -> None: ...


class JsonFileProcessedTaskStore:
    def __init__(self, path: Path):
        self._path = path

    def load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save(self, records: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(records), encoding="utf-8")
