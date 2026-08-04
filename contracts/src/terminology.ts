/**
 * 구판 용어 잔존 검사용 (지시문 11절 "구형 용어 잔존 0건", 260803-1438 용어 일치 검토 기준).
 * 코드/메시지 페이로드에 아래 문자열이 키로 등장하면 계약 위반.
 */
export const FORBIDDEN_LEGACY_TERMS = [
  "ToolCode",
  "toolCode",
  "ExecutionTarget",
  "executionTarget",
  "missingArguments",
  "allowedTools",
  "rootId",
  "indexedRoots",
  "capabilities",
  "CLOUD_SYNC",
  "AI_WORKER",
] as const;

export function findForbiddenTerms(source: string): string[] {
  return FORBIDDEN_LEGACY_TERMS.filter((term) => source.includes(term));
}
