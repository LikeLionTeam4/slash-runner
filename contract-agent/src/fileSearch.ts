import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { toIsoKst } from "@slash-agent/contracts";

export interface FileSearchHit {
  name: string;
  relativePath: string;
  sizeBytes: number;
  modifiedAt: string;
}

export interface FileSearchResult {
  items: FileSearchHit[];
  returnedCount: number;
  truncated: boolean;
}

function walk(dir: string, out: string[]): void {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith(".")) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else out.push(full);
  }
}

/**
 * fixtures/search-folder 안에서만 검색하고 상대 경로만 반환한다 (지시문 8절: 로컬 절대 경로 미전송).
 * 응답 형태는 메시지 프로토콜 문서 §8.7의 FILE_SEARCH RESULT 예시(items[].sizeBytes,
 * returnedCount, truncated)와 정합한다.
 */
export function searchFiles(rootDir: string, query: string, limit = 20): FileSearchResult {
  const allFiles: string[] = [];
  walk(rootDir, allFiles);
  const lowerQuery = query.toLowerCase();
  const matches = allFiles.filter((path) => path.toLowerCase().includes(lowerQuery));
  const items = matches.slice(0, limit).map((path) => {
    const stats = statSync(path);
    return {
      name: path.split("/").pop() ?? path,
      relativePath: relative(rootDir, path),
      sizeBytes: stats.size,
      modifiedAt: toIsoKst(stats.mtime),
    };
  });
  return { items, returnedCount: items.length, truncated: matches.length > limit };
}
