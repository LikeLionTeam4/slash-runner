import { z } from "zod";
import { TASK_STATUSES, DEVICE_STATUSES, LLM_READINESS_STATES } from "./enums.js";

/** 사용자 WSS (/ws/user) — Agent WSS와 별개의 단순 알림 채널. 영구 원장이 아니며 REST가 진실 소스. */

export const connectedEventSchema = z.object({
  type: z.literal("CONNECTED"),
  connectionId: z.string(),
  serverTime: z.string().datetime({ offset: true }),
});

export const taskStatusChangedEventSchema = z.object({
  type: z.literal("TASK_STATUS_CHANGED"),
  taskId: z.string().uuid(),
  from: z.enum(TASK_STATUSES),
  to: z.enum(TASK_STATUSES),
  occurredAt: z.string().datetime({ offset: true }),
});

export const taskResultAvailableEventSchema = z.object({
  type: z.literal("TASK_RESULT_AVAILABLE"),
  taskId: z.string().uuid(),
  status: z.enum(TASK_STATUSES),
  resultPreview: z.string().nullable(),
});

export const deviceStatusChangedEventSchema = z.object({
  type: z.literal("DEVICE_STATUS_CHANGED"),
  deviceId: z.string(),
  status: z.enum(DEVICE_STATUSES),
  lastSeenAt: z.string().datetime({ offset: true }).nullable(),
});

export const llmReadinessChangedEventSchema = z.object({
  type: z.literal("LLM_READINESS_CHANGED"),
  state: z.enum(LLM_READINESS_STATES),
  acceptingJobs: z.boolean(),
});

export const pingEventSchema = z.object({
  type: z.literal("PING"),
  sentAt: z.string().datetime({ offset: true }),
});

export const pongEventSchema = z.object({
  type: z.literal("PONG"),
  sentAt: z.string().datetime({ offset: true }),
});

export const userEventSchema = z.discriminatedUnion("type", [
  connectedEventSchema,
  taskStatusChangedEventSchema,
  taskResultAvailableEventSchema,
  deviceStatusChangedEventSchema,
  llmReadinessChangedEventSchema,
  pingEventSchema,
  pongEventSchema,
]);
export type UserEvent = z.infer<typeof userEventSchema>;
