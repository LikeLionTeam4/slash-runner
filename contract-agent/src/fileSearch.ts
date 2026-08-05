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
 *
 * macOS(HFS+/APFS)는 `readdirSync`로 한글 등 조합 가능한 문자가 포함된 파일명을 NFD(자모
 * 분리형)로 돌려준다. 반면 REST/WSS로 들어오는 검색어는 보통 NFC(완성형)라, 정규화 없이
 * 비교하면 눈으로는 같은 문자열인데도 항상 매칭에 실패한다 — 파일명·검색어 양쪽을 NFC로
 * 맞춘 뒤 비교하고, 응답에 내보내는 이름도 NFC로 정규화해 일관되게 만든다.
 */
export function searchFiles(rootDir: string, query: string, limit = 20): FileSearchResult {
  const allFiles: string[] = [];
  walk(rootDir, allFiles);
  const lowerQuery = query.normalize("NFC").toLowerCase();
  const matches = allFiles.filter((path) => path.normalize("NFC").toLowerCase().includes(lowerQuery));
  const items = matches.slice(0, limit).map((path) => {
    const stats = statSync(path);
    return {
      name: (path.split("/").pop() ?? path).normalize("NFC"),
      relativePath: relative(rootDir, path).normalize("NFC"),
      sizeBytes: stats.size,
      modifiedAt: toIsoKst(stats.mtime),
    };
  });
  return { items, returnedCount: items.length, truncated: matches.length > limit };
}
