import { z } from "zod";
import {
  TASK_TYPES,
  PROCESSING_ROUTES,
  TASK_STATUSES,
  DEVICE_STATUSES,
  DEVICE_OS_VALUES,
  ARCHITECTURE_VALUES,
  LLM_READINESS_STATES,
  REASON_CODES,
} from "./enums.js";

/**
 * REST 계약 (docs/260804-1123_SLASH-메시지프로토콜.md 기준으로 정합).
 * 모든 성공 응답은 {data, meta}, 모든 에러 응답은 {error, meta} 봉투를 쓴다 — §3.3, mock-api/src/envelope.ts.
 * `/test/*` 시험 전용 엔드포인트는 이 문서가 다루는 범위 밖이라 봉투를 적용하지 않는다.
 */

export interface ApiMeta {
  requestId: string;
  serverTime: string;
}
export interface ApiSuccessEnvelope<T> {
  data: T;
  meta: ApiMeta;
}
export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
  meta: ApiMeta;
}
export interface ApiListEnvelope<T> {
  data: T[];
  meta: ApiMeta & { nextCursor: string | null };
}

export const createTaskRequestSchema = z.object({
  text: z.string().min(1),
  selectedDeviceId: z.string().nullable().optional(),
  clientTimeZone: z.string().optional(),
});
export type CreateTaskRequest = z.infer<typeof createTaskRequestSchema>;

/** GET /tasks/{taskId}/events 공개 응답 형태 (메시지 프로토콜 문서 §4.6과 정합). */
export const taskEventSchema = z.object({
  eventId: z.string().uuid(),
  taskId: z.string().uuid(),
  fromStatus: z.enum(TASK_STATUSES).nullable(),
  toStatus: z.enum(TASK_STATUSES).nullable(),
  reasonCode: z.string().nullable(),
  message: z.string().nullable(),
  occurredAt: z.string().datetime({ offset: true }),
});
export type TaskEvent = z.infer<typeof taskEventSchema>;

export const taskSchema = z.object({
  taskId: z.string().uuid(),
  userId: z.string(),
  deviceId: z.string().nullable(),
  inputText: z.string(),
  taskType: z.enum(TASK_TYPES).nullable(),
  processingRoute: z.enum(PROCESSING_ROUTES).nullable(),
  status: z.enum(TASK_STATUSES),
  parameters: z.record(z.unknown()),
  missingRequiredParameters: z.array(z.string()),
  result: z.record(z.unknown()).nullable(),
  errorCode: z.string().nullable(),
  correlationId: z.string().uuid(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
});
export type Task = z.infer<typeof taskSchema>;

export const createTaskResponseSchema = z.object({
  taskId: z.string().uuid(),
  status: z.literal("ANALYZING"),
  statusUrl: z.string(),
});
export type CreateTaskResponse = z.infer<typeof createTaskResponseSchema>;

export const deviceSchema = z.object({
  deviceId: z.string(),
  name: z.string(),
  os: z.enum(DEVICE_OS_VALUES),
  status: z.enum(DEVICE_STATUSES),
  supportedTaskTypes: z.array(z.enum(TASK_TYPES)),
  lastSeenAt: z.string().datetime({ offset: true }).nullable(),
});
export type Device = z.infer<typeof deviceSchema>;

export const taskTypeInfoSchema = z.object({
  code: z.enum(TASK_TYPES),
  slashCommand: z.string(),
  processingRoute: z.enum(PROCESSING_ROUTES),
  requiredParameters: z.array(z.string()),
  enabled: z.boolean(),
});
export type TaskTypeInfo = z.infer<typeof taskTypeInfoSchema>;

export const pairingRequestResponseSchema = z.object({
  pairingRequestId: z.string(),
  pairingCode: z.string().length(6),
  expiresAt: z.string().datetime({ offset: true }),
});

export const agentPairRequestSchema = z.object({
  pairingCode: z.string().length(6),
  publicKey: z.string(),
  device: z.object({
    name: z.string(),
    os: z.enum(DEVICE_OS_VALUES),
    architecture: z.enum(ARCHITECTURE_VALUES),
    osVersion: z.string(),
    agentVersion: z.string(),
  }),
  supportedTaskTypes: z.array(z.enum(TASK_TYPES)),
});
export type AgentPairRequest = z.infer<typeof agentPairRequestSchema>;

export const agentPairResponseSchema = z.object({
  pairingSessionId: z.string(),
  deviceId: z.string(),
  challengeId: z.string().uuid(),
  nonce: z.string(),
  expiresAt: z.string().datetime({ offset: true }),
});
export type AgentPairResponse = z.infer<typeof agentPairResponseSchema>;

export const agentPairVerifyRequestSchema = z.object({
  pairingSessionId: z.string(),
  challengeId: z.string().uuid(),
  signature: z.string(),
});
export type AgentPairVerifyRequest = z.infer<typeof agentPairVerifyRequestSchema>;

export const agentPairVerifyResponseSchema = z.object({
  deviceToken: z.string(),
  expiresIn: z.number().int().positive(),
  issuedAt: z.string().datetime({ offset: true }),
  wsUrl: z.string(),
});
export type AgentPairVerifyResponse = z.infer<typeof agentPairVerifyResponseSchema>;

/** POST /agent/sessions/refresh — 기존 토큰이 아니라 서명으로 소유를 재증명한다 (메시지 프로토콜 문서 §8.1 3단계). */
export const agentSessionRefreshRequestSchema = z.object({
  deviceId: z.string(),
  refreshNonce: z.string().uuid(),
  requestedAt: z.string().datetime({ offset: true }),
  signature: z.string(),
});
export type AgentSessionRefreshRequest = z.infer<typeof agentSessionRefreshRequestSchema>;

export const agentSessionRefreshResponseSchema = z.object({
  deviceToken: z.string(),
  expiresIn: z.number().int().positive(),
  issuedAt: z.string().datetime({ offset: true }),
});
export type AgentSessionRefreshResponse = z.infer<typeof agentSessionRefreshResponseSchema>;

/** 서명 대상 문자열: deviceId + ":" + refreshNonce + ":" + requestedAt (메시지 프로토콜 문서 §8.1) */
export function buildRefreshSigningPayload(params: {
  deviceId: string;
  refreshNonce: string;
  requestedAt: string;
}): string {
  return `${params.deviceId}:${params.refreshNonce}:${params.requestedAt}`;
}

/** POST /ws-tickets — 30초·1회용 사용자 WSS 접속권 (메시지 프로토콜 문서 §4.4). */
export const wsTicketResponseSchema = z.object({
  ticket: z.string(),
  expiresIn: z.number().int().positive(),
  wsUrl: z.string(),
});
export type WsTicketResponse = z.infer<typeof wsTicketResponseSchema>;

/** PATCH /devices/{deviceId} 요청 본문 (메시지 프로토콜 문서 §4.3). */
export const devicePatchRequestSchema = z.object({
  name: z.string().min(1).max(100),
});
export type DevicePatchRequest = z.infer<typeof devicePatchRequestSchema>;

export const historyItemSchema = z.object({
  taskId: z.string().uuid(),
  taskType: z.enum(TASK_TYPES).nullable(),
  status: z.enum(TASK_STATUSES),
  requestSummary: z.string(),
  createdAt: z.string().datetime({ offset: true }),
});
export type HistoryItem = z.infer<typeof historyItemSchema>;

export const taskAvailabilitySchema = z.object({
  available: z.boolean(),
  reasonCode: z.enum(REASON_CODES).nullable(),
  supportedTaskTypes: z.array(z.enum(TASK_TYPES)),
});
export type TaskAvailability = z.infer<typeof taskAvailabilitySchema>;

export const llmReadinessResponseSchema = z.object({
  state: z.enum(LLM_READINESS_STATES),
  acceptingJobs: z.boolean(),
  model: z.string(),
  checkedAt: z.string().datetime({ offset: true }),
});

export const testLoginResponseSchema = z.object({
  userId: z.string(),
  email: z.string(),
  displayName: z.string(),
  token: z.string(),
});
export type TestLoginResponse = z.infer<typeof testLoginResponseSchema>;

export const IDEMPOTENCY_KEY_HEADER = "Idempotency-Key";
export const CORRELATION_ID_HEADER = "X-Correlation-Id";
