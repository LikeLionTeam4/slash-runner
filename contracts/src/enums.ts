export const TASK_TYPES = [
  "WEATHER_LOOKUP",
  "FILE_SEARCH",
  "SYSTEM_STATUS",
  "TEXT_SUMMARY",
  "CODE_ANALYSIS",
  // COMMAND — slash-*-test 브랜치 전용 임시 TaskType. 실제 확정 스펙(6종)엔 없다.
  // 프론트→백엔드→로컬 에이전트 종단 통신을 확인하기 위한 것으로, 로컬 에이전트가
  // 백그라운드로 명령어 문자열을 받아 처리하고 결과를 돌려주는 용도다. 지금은 받은
  // 명령어를 그대로 에코하는 최소 구현만 있다. main/dev 브랜치로 올리지 않는다.
  "COMMAND",
] as const;
export type TaskType = (typeof TASK_TYPES)[number];

export const PROCESSING_ROUTES = [
  "BACKEND_SERVICE",
  "LOCAL_AGENT",
  "LLM_SERVICE",
] as const;
export type ProcessingRoute = (typeof PROCESSING_ROUTES)[number];

export const TASK_STATUSES = [
  "CREATED",
  "ANALYZING",
  "NEEDS_CLARIFICATION",
  "WAITING_FOR_DEVICE",
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "EXPIRED",
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export const DEVICE_STATUSES = [
  "ONLINE",
  "READY",
  "BUSY",
  "OFFLINE",
  "REVOKED",
] as const;
export type DeviceStatus = (typeof DEVICE_STATUSES)[number];

export const DEVICE_OS_VALUES = ["WINDOWS", "MACOS"] as const;
export type DeviceOs = (typeof DEVICE_OS_VALUES)[number];

export const ARCHITECTURE_VALUES = ["X86_64", "ARM64"] as const;
export type Architecture = (typeof ARCHITECTURE_VALUES)[number];

export const ASYNC_JOB_STATUSES = [
  "PENDING",
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "EXPIRED",
] as const;
export type AsyncJobStatus = (typeof ASYNC_JOB_STATUSES)[number];

export const AGENT_DISPATCH_STATUSES = [
  "PENDING",
  "DISPATCHED",
  "ACKNOWLEDGED",
  "COMPLETED",
  "FAILED",
  "EXPIRED",
] as const;
export type AgentDispatchStatus = (typeof AGENT_DISPATCH_STATUSES)[number];

/** ACK.reasonCode / RESULT.error.code 공통 값 (지시문 11절, 메시지 스펙 §6.2) */
export const REASON_CODES = [
  "DEVICE_BUSY",
  "TASK_TYPE_NOT_SUPPORTED",
  "INVALID_PARAMETERS",
  "SEARCH_FOLDER_NOT_FOUND",
  "WORKSPACE_NOT_FOUND",
  "CODE_AGENT_NOT_CONFIGURED",
  "TASK_EXPIRED",
  "POLICY_DENIED",
] as const;
export type ReasonCode = (typeof REASON_CODES)[number];

export const PROTOCOL_ERROR_CODES = [
  "UNSUPPORTED_SCHEMA_VERSION",
  "INVALID_MESSAGE",
  "AUTHENTICATION_FAILED",
  "CHALLENGE_EXPIRED",
  "CHALLENGE_REUSED",
  "INVALID_CONNECTION_STATE",
  "DEVICE_REVOKED",
  "AGENT_VERSION_UNSUPPORTED",
  "PLATFORM_UNSUPPORTED",
] as const;
export type ProtocolErrorCode = (typeof PROTOCOL_ERROR_CODES)[number];

export const WORKSPACE_TYPES = ["GIT_REPOSITORY", "DIRECTORY"] as const;
export type WorkspaceType = (typeof WORKSPACE_TYPES)[number];

export const CODE_ADAPTERS = ["CLAUDE_CODE", "CODEX"] as const;
export type CodeAdapter = (typeof CODE_ADAPTERS)[number];

export const LLM_READINESS_STATES = [
  "OFFLINE",
  "STARTING",
  "LOADING",
  "READY",
  "DRAINING",
  "ERROR",
] as const;
export type LlmReadinessState = (typeof LLM_READINESS_STATES)[number];

/** 지시문 7절 표: TaskType -> ProcessingRoute 고정 매핑 */
export const TASK_TYPE_ROUTE: Record<TaskType, ProcessingRoute> = {
  WEATHER_LOOKUP: "BACKEND_SERVICE",
  FILE_SEARCH: "LOCAL_AGENT",
  SYSTEM_STATUS: "LOCAL_AGENT",
  TEXT_SUMMARY: "LLM_SERVICE",
  CODE_ANALYSIS: "LOCAL_AGENT",
  COMMAND: "LOCAL_AGENT",
};

/** TaskType 별 필수 parameters 키 목록 (조사 결과 §3 기준) */
export const TASK_TYPE_REQUIRED_PARAMETERS: Record<TaskType, string[]> = {
  WEATHER_LOOKUP: ["location"],
  FILE_SEARCH: ["query", "searchFolderId"],
  SYSTEM_STATUS: [],
  TEXT_SUMMARY: ["text"],
  CODE_ANALYSIS: ["workspaceId"],
  COMMAND: ["command"],
};

/** P0 = 항상 활성, P1 = 기본 비활성(조건부, /code) */
export const TASK_TYPE_TIER: Record<TaskType, "P0" | "P1"> = {
  WEATHER_LOOKUP: "P0",
  FILE_SEARCH: "P0",
  SYSTEM_STATUS: "P0",
  TEXT_SUMMARY: "P0",
  CODE_ANALYSIS: "P1",
  COMMAND: "P0",
};

export const SLASH_COMMAND_TASK_TYPE: Record<string, TaskType> = {
  weather: "WEATHER_LOOKUP",
  file: "FILE_SEARCH",
  status: "SYSTEM_STATUS",
  summary: "TEXT_SUMMARY",
  code: "CODE_ANALYSIS",
  command: "COMMAND",
};
