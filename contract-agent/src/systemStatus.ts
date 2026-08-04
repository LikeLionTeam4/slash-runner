import os from "node:os";
import { execSync } from "node:child_process";
import { nowIsoKst } from "@slash-agent/contracts";

export interface SystemStatusResult {
  cpuPercent: number;
  memoryPercent: number;
  memoryTotalMb: number;
  memoryUsedMb: number;
  diskPercent: number | null;
  diskTotalMb: number | null;
  diskUsedMb: number | null;
  collectedAt: string;
}

function cpuPercentApprox(): number {
  const load1 = os.loadavg()[0] ?? 0;
  const cores = os.cpus().length || 1;
  return Math.max(0, Math.min(100, Math.round((load1 / cores) * 100)));
}

function diskUsage(): { percent: number; totalMb: number; usedMb: number } | null {
  try {
    const output = execSync("df -k /").toString();
    const lines = output.trim().split("\n");
    const parts = lines[lines.length - 1].trim().split(/\s+/);
    const totalKb = Number(parts[1]);
    const usedKb = Number(parts[2]);
    if (!totalKb) return null;
    return {
      percent: Math.round((usedKb / totalKb) * 100),
      totalMb: Math.round(totalKb / 1024),
      usedMb: Math.round(usedKb / 1024),
    };
  } catch {
    return null;
  }
}

/** 이 macOS 실기기의 실제 CPU/메모리/디스크 값을 수집한다 (Gemma/AWS 같은 실 서비스 흉내 없음). */
export function collectSystemStatus(): SystemStatusResult {
  const totalMem = os.totalmem();
  const freeMem = os.freemem();
  const usedMem = totalMem - freeMem;
  const disk = diskUsage();
  return {
    cpuPercent: cpuPercentApprox(),
    memoryPercent: Math.round((usedMem / totalMem) * 100),
    memoryTotalMb: Math.round(totalMem / 1024 / 1024),
    memoryUsedMb: Math.round(usedMem / 1024 / 1024),
    diskPercent: disk?.percent ?? null,
    diskTotalMb: disk?.totalMb ?? null,
    diskUsedMb: disk?.usedMb ?? null,
    collectedAt: nowIsoKst(),
  };
}
