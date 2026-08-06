import { AckMessage, ResultMessage } from "@slash-agent/contracts";

/**
 * 중복 실행 방지·RESULT 재전송용 처리 이력 (메시지 프로토콜 문서 §6.1 processed_tasks 대응)
 * - SQLite `processed_tasks` 표에 대응하는 개념, 실제 SQLite는 미사용
 * - 저장 방식은 구현체(agent-app/cli)에 위임
 */
export interface CachedDispatchResult {
  ack: Omit<AckMessage, "schemaVersion" | "eventId" | "sentAt">;
  result: Omit<ResultMessage, "schemaVersion" | "eventId" | "sentAt">;
  acked: boolean;
  /** 완료 시각 — RESULT_ACK 후 정리(prune) 판단 기준 */
  completedAt: string;
}

export interface ProcessedTaskStore {
  load(): Promise<Record<string, CachedDispatchResult>>;
  save(records: Record<string, CachedDispatchResult>): Promise<void>;
}
