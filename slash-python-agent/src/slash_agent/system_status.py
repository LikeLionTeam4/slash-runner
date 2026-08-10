"""SYSTEM_STATUS 작업 실행 — systemStatus.ts 대응. 이 실기기의 CPU/메모리/디스크 실측값."""

from __future__ import annotations

import shutil

import psutil

from .protocol import now_iso_kst


def collect_system_status() -> dict:
    vm = psutil.virtual_memory()
    try:
        disk = shutil.disk_usage("/")
        disk_percent = round(disk.used / disk.total * 100)
        disk_total_mb = disk.total // (1024 * 1024)
        disk_used_mb = disk.used // (1024 * 1024)
    except OSError:
        disk_percent = disk_total_mb = disk_used_mb = None

    return {
        "cpuPercent": psutil.cpu_percent(interval=0.1),
        "memoryPercent": round(vm.percent),
        "memoryTotalMb": vm.total // (1024 * 1024),
        "memoryUsedMb": (vm.total - vm.available) // (1024 * 1024),
        "diskPercent": disk_percent,
        "diskTotalMb": disk_total_mb,
        "diskUsedMb": disk_used_mb,
        "collectedAt": now_iso_kst(),
    }
