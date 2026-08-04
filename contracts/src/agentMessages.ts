import { z } from "zod";
import {
  TASK_TYPES,
  DEVICE_OS_VALUES,
  ARCHITECTURE_VALUES,
  REASON_CODES,
  PROTOCOL_ERROR_CODES,
  WORKSPACE_TYPES,
  CODE_ADAPTERS,
} from "./enums.js";

/**
 * Agent WSS 계약 (docs/260803-1524_SLASH_BACKEND_AGENT_MESSAGE_SPEC.md 기준).
 * 모든 메시지는 payload 래핑 없이 평탄한 구조이며 공통 필드를 공유한다.
 */

export const AGENT_SCHEMA_VERSION = "1.0";

const commonFields = {
  schemaVersion: z.literal(AGENT_SCHEMA_VERSION),
  eventId: z.string().uuid(),
  sentAt: z.string().datetime({ offset: true }),
};

const taskFields = {
  taskId: z.string().uuid(),
  dispatchId: z.string().uuid(),
  correlationId: z.string().uuid(),
};

export const payloadSha256Schema = z
  .string()
  .regex(/^[0-9a-fA-F]{64}$/, "payloadSha256 must be a 64-char hex string");

export const helloMessageSchema = z.object({
  ...commonFields,
  type: z.literal("HELLO"),
  deviceId: z.string(),
  agentVersion: z.string(),
  os: z.enum(DEVICE_OS_VALUES),
  architecture: z.enum(ARCHITECTURE_VALUES),
  osVersion: z.string(),
  supportedTaskTypes: z.array(z.enum(TASK_TYPES)),
});
export type HelloMessage = z.infer<typeof helloMessageSchema>;

export const challengeMessageSchema = z.object({
  ...commonFields,
  type: z.literal("CHALLENGE"),
  challengeId: z.string().uuid(),
  nonce: z.string(),
  expiresAt: z.string().datetime({ offset: true }),
});
export type ChallengeMessage = z.infer<typeof challengeMessageSchema>;

export const authMessageSchema = z.object({
  ...commonFields,
  type: z.literal("AUTH"),
  challengeId: z.string().uuid(),
  signature: z.string(),
});
export type AuthMessage = z.infer<typeof authMessageSchema>;

export const searchFolderSchema = z.object({
  searchFolderId: z.string(),
  displayName: z.string(),
  indexStatus: z.enum(["INDEXED", "INDEXING", "UNAVAILABLE"]),
});
export type SearchFolder = z.infer<typeof searchFolderSchema>;

export const projectWorkspaceSchema = z.object({
  workspaceId: z.string(),
  displayName: z.string(),
  workspaceType: z.enum(WORKSPACE_TYPES),
  availableCodeAdapters: z.array(z.enum(CODE_ADAPTERS)),
});
export type ProjectWorkspace = z.infer<typeof projectWorkspaceSchema>;

export const readyMessageSchema = z.object({
  ...commonFields,
  type: z.literal("READY"),
  maxConcurrentTasks: z.number().int().positive(),
  supportedTaskTypes: z.array(z.enum(TASK_TYPES)),
  searchFolders: z.array(searchFolderSchema),
  projectWorkspaces: z.array(projectWorkspaceSchema),
});
export type ReadyMessage = z.infer<typeof readyMessageSchema>;

export const heartbeatMessageSchema = z.object({
  ...commonFields,
  type: z.literal("HEARTBEAT"),
  deviceId: z.string(),
  cpuPercent: z.number().min(0).max(100),
  memoryPercent: z.number().min(0).max(100),
  runningTaskId: z.string().uuid().nullable(),
});
export type HeartbeatMessage = z.infer<typeof heartbeatMessageSchema>;

export const taskMessageSchema = z.object({
  ...commonFields,
  ...taskFields,
  type: z.literal("TASK"),
  taskType: z.enum(TASK_TYPES),
  parameters: z.record(z.unknown()),
  expiresAt: z.string().datetime({ offset: true }),
  payloadSha256: payloadSha256Schema,
});
export type TaskMessage = z.infer<typeof taskMessageSchema>;

export const ackMessageSchema = z.object({
  ...commonFields,
  ...taskFields,
  type: z.literal("ACK"),
  accepted: z.boolean(),
  reasonCode: z.enum(REASON_CODES).nullable(),
  acknowledgedAt: z.string().datetime({ offset: true }),
});
export type AckMessage = z.infer<typeof ackMessageSchema>;

export const progressMessageSchema = z.object({
  ...commonFields,
  ...taskFields,
  type: z.literal("PROGRESS"),
  stage: z.string(),
  percent: z.number().min(0).max(100).optional(),
  message: z.string().optional(),
});
export type ProgressMessage = z.infer<typeof progressMessageSchema>;

export const resultErrorSchema = z.object({
  code: z.enum(REASON_CODES),
  message: z.string(),
  retryable: z.boolean(),
});

export const resultMessageSchema = z.object({
  ...commonFields,
  ...taskFields,
  type: z.literal("RESULT"),
  status: z.enum(["SUCCEEDED", "FAILED"]),
  result: z.record(z.unknown()).nullable(),
  error: resultErrorSchema.nullable(),
  startedAt: z.string().datetime({ offset: true }),
  finishedAt: z.string().datetime({ offset: true }),
});
export type ResultMessage = z.infer<typeof resultMessageSchema>;

export const resultAckMessageSchema = z.object({
  ...commonFields,
  ...taskFields,
  type: z.literal("RESULT_ACK"),
  persisted: z.boolean(),
  taskStatus: z.string(),
});
export type ResultAckMessage = z.infer<typeof resultAckMessageSchema>;

export const protocolErrorMessageSchema = z.object({
  ...commonFields,
  type: z.literal("PROTOCOL_ERROR"),
  code: z.enum(PROTOCOL_ERROR_CODES),
  message: z.string(),
  relatedEventId: z.string().uuid().nullable(),
  closeConnection: z.boolean(),
});
export type ProtocolErrorMessage = z.infer<typeof protocolErrorMessageSchema>;

export const agentMessageSchema = z.discriminatedUnion("type", [
  helloMessageSchema,
  challengeMessageSchema,
  authMessageSchema,
  readyMessageSchema,
  heartbeatMessageSchema,
  taskMessageSchema,
  ackMessageSchema,
  progressMessageSchema,
  resultMessageSchema,
  resultAckMessageSchema,
  protocolErrorMessageSchema,
]);
export type AgentMessage = z.infer<typeof agentMessageSchema>;

export const AGENT_MESSAGE_TYPES = [
  "HELLO",
  "CHALLENGE",
  "AUTH",
  "READY",
  "HEARTBEAT",
  "TASK",
  "ACK",
  "PROGRESS",
  "RESULT",
  "RESULT_ACK",
  "PROTOCOL_ERROR",
] as const;

/** 서명 대상 문자열: challengeId + ":" + nonce + ":" + deviceId (메시지 스펙 §3) */
export function buildChallengeSigningPayload(params: {
  challengeId: string;
  nonce: string;
  deviceId: string;
}): string {
  return `${params.challengeId}:${params.nonce}:${params.deviceId}`;
}
