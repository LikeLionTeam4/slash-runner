import WebSocket from "ws";
import {
  AgentMessage,
  agentMessageSchema,
  AGENT_SCHEMA_VERSION,
  TaskType,
  ReasonCode,
  HelloMessage,
  ReadyMessage,
  HeartbeatMessage,
  AuthMessage,
  AckMessage,
  ResultMessage,
  ProgressMessage,
  buildChallengeSigningPayload,
  nowIsoKst,
} from "@slash-agent/contracts";
import { generateAgentKeyPair, signPayload } from "./agentCrypto.js";
import { pairAgent, verifyPairing } from "./pairingClient.js";
import { collectSystemStatus } from "./systemStatus.js";
import { searchFiles } from "./fileSearch.js";
import { randomUUID } from "node:crypto";

export const SEARCH_FOLDER_ID = "sf-fixtures-01";
// COMMAND는 slash-*-test 브랜치 전용 임시 TaskType이다 (contracts/src/enums.ts 주석 참고).
export const SUPPORTED_TASK_TYPES: TaskType[] = ["FILE_SEARCH", "SYSTEM_STATUS", "COMMAND"];

export interface ContractAgentOptions {
  apiBaseUrl: string;
  /** 정상 페어링 경로. `presetDeviceId`/`presetDeviceToken`을 주면 이 값은 무시되고 HTTP 페어링을 건너뛴다. */
  pairingCode?: string;
  searchFolderRoot: string;
  deviceName?: string;
  heartbeatIntervalMs?: number;
  log?: (line: string) => void;
  /** 시험 전용: 이미 발급된 deviceId/deviceToken을 직접 주입해 HTTP 페어링 단계를 생략한다. */
  presetDeviceId?: string;
  presetDeviceToken?: string;
}

interface CachedDispatchResult {
  ack: Omit<AckMessage, "schemaVersion" | "eventId" | "sentAt">;
  result: Omit<ResultMessage, "schemaVersion" | "eventId" | "sentAt">;
  acked: boolean;
}

export type ContractAgentState = "CONNECTING" | "AUTHENTICATING" | "READY" | "OFFLINE" | "STOPPED";

interface ResolvedContractAgentOptions {
  apiBaseUrl: string;
  pairingCode?: string;
  searchFolderRoot: string;
  deviceName: string;
  heartbeatIntervalMs: number;
  log: (line: string) => void;
  presetDeviceId?: string;
  presetDeviceToken?: string;
}

export class ContractAgent {
  private readonly options: ResolvedContractAgentOptions;
  private readonly keyPair = generateAgentKeyPair();
  private socket: WebSocket | null = null;
  private deviceId: string | null = null;
  private deviceToken: string | null = null;
  private challengeId: string | null = null;
  private nonce: string | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectAttempt = 0;
  private stopped = false;
  private state: ContractAgentState = "CONNECTING";
  private readonly resultCache = new Map<string, CachedDispatchResult>();
  private readyWaiters: Array<() => void> = [];

  constructor(options: ContractAgentOptions) {
    this.options = {
      apiBaseUrl: options.apiBaseUrl,
      pairingCode: options.pairingCode,
      searchFolderRoot: options.searchFolderRoot,
      deviceName: options.deviceName ?? "contract-agent-simulator",
      heartbeatIntervalMs: options.heartbeatIntervalMs ?? 30_000,
      log: options.log ?? (() => {}),
      presetDeviceId: options.presetDeviceId,
      presetDeviceToken: options.presetDeviceToken,
    };
    if (options.presetDeviceId && options.presetDeviceToken) {
      this.deviceId = options.presetDeviceId;
      this.deviceToken = options.presetDeviceToken;
    }
  }

  getState(): ContractAgentState {
    return this.state;
  }

  getDeviceId(): string | null {
    return this.deviceId;
  }

  async start(): Promise<void> {
    await this.pairIfNeeded();
    void this.connectionLoop();
  }

  waitUntilReady(timeoutMs = 15_000): Promise<void> {
    if (this.state === "READY") return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("contract-agent READY 대기 타임아웃")), timeoutMs);
      this.readyWaiters.push(() => {
        clearTimeout(timer);
        resolve();
      });
    });
  }

  stop(): void {
    this.stopped = true;
    this.state = "STOPPED";
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.socket?.close();
  }

  private log(line: string): void {
    this.options.log(`[contract-agent] ${line}`);
  }

  private async pairIfNeeded(): Promise<void> {
    if (this.deviceToken) return;
    if (!this.options.pairingCode) {
      throw new Error("pairingCode 또는 presetDeviceId/presetDeviceToken 중 하나는 반드시 필요합니다.");
    }
    const pairResponse = await pairAgent({
      apiBaseUrl: this.options.apiBaseUrl,
      pairingCode: this.options.pairingCode,
      publicKeyBase64: this.keyPair.publicKeyBase64,
      deviceName: this.options.deviceName,
      supportedTaskTypes: SUPPORTED_TASK_TYPES,
    });
    const signature = signPayload(
      this.keyPair.privateKey,
      buildChallengeSigningPayload({
        challengeId: pairResponse.challengeId,
        nonce: pairResponse.nonce,
        deviceId: pairResponse.deviceId,
      })
    );
    const verifyResponse = await verifyPairing(this.options.apiBaseUrl, {
      pairingSessionId: pairResponse.pairingSessionId,
      challengeId: pairResponse.challengeId,
      signature,
    });
    this.deviceId = pairResponse.deviceId;
    this.deviceToken = verifyResponse.deviceToken;
    this.log(`페어링 완료 deviceId=${this.deviceId}`);
  }

  private async connectionLoop(): Promise<void> {
    while (!this.stopped) {
      try {
        await this.connectOnce();
      } catch (error) {
        this.log(`연결 오류: ${String(error)}`);
      }
      if (this.stopped) return;
      this.state = "OFFLINE";
      const delayMs = Math.min(30_000, 1000 * 2 ** this.reconnectAttempt) + Math.floor(Math.random() * 250);
      this.reconnectAttempt += 1;
      this.log(`재연결 대기 ${delayMs}ms`);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  private connectOnce(): Promise<void> {
    return new Promise((resolve, reject) => {
      const wsUrl = `${this.options.apiBaseUrl.replace(/^http/, "ws")}/ws/agent`;
      const socket = new WebSocket(wsUrl, { headers: { Authorization: `Bearer ${this.deviceToken}` } });
      this.socket = socket;
      this.state = "CONNECTING";

      socket.on("open", () => {
        this.reconnectAttempt = 0;
        this.state = "AUTHENTICATING";
        this.send(socket, this.buildHello());
      });

      socket.on("message", (raw) => {
        this.handleMessage(socket, raw.toString());
      });

      socket.on("close", () => {
        if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
        resolve();
      });

      socket.on("error", (error) => {
        reject(error);
      });
    });
  }

  private send(socket: WebSocket, message: Omit<AgentMessage, "schemaVersion" | "eventId" | "sentAt">): void {
    const full = {
      schemaVersion: AGENT_SCHEMA_VERSION,
      eventId: randomUUID(),
      sentAt: nowIsoKst(),
      ...message,
    } as AgentMessage;
    socket.send(JSON.stringify(full));
  }

  private buildHello(): Omit<HelloMessage, "schemaVersion" | "eventId" | "sentAt"> {
    return {
      type: "HELLO",
      deviceId: this.deviceId!,
      agentVersion: "contract-agent-simulator/0.1.0",
      os: "MACOS",
      architecture: process.arch === "arm64" ? "ARM64" : "X86_64",
      osVersion: process.platform,
      supportedTaskTypes: SUPPORTED_TASK_TYPES,
    };
  }

  private buildReady(): Omit<ReadyMessage, "schemaVersion" | "eventId" | "sentAt"> {
    return {
      type: "READY",
      maxConcurrentTasks: 1,
      supportedTaskTypes: SUPPORTED_TASK_TYPES,
      searchFolders: [{ searchFolderId: SEARCH_FOLDER_ID, displayName: "테스트 검색 폴더", indexStatus: "INDEXED" }],
      projectWorkspaces: [],
    };
  }

  private handleMessage(socket: WebSocket, raw: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return;
    }
    const result = agentMessageSchema.safeParse(parsed);
    if (!result.success) {
      this.log(`알 수 없는 메시지 수신 무시: ${result.error.issues[0]?.message}`);
      return;
    }
    const message = result.data;

    switch (message.type) {
      case "CHALLENGE": {
        this.challengeId = message.challengeId;
        this.nonce = message.nonce;
        const signature = signPayload(
          this.keyPair.privateKey,
          buildChallengeSigningPayload({
            challengeId: message.challengeId,
            nonce: message.nonce,
            deviceId: this.deviceId!,
          })
        );
        const auth: Omit<AuthMessage, "schemaVersion" | "eventId" | "sentAt"> = {
          type: "AUTH",
          challengeId: message.challengeId,
          signature,
        };
        this.send(socket, auth);
        this.send(socket, this.buildReady());
        this.state = "READY";
        this.startHeartbeat(socket);
        this.log("READY 전송 완료");
        this.readyWaiters.splice(0).forEach((resolve) => resolve());
        this.resendUnackedResults(socket);
        return;
      }
      case "RESULT_ACK": {
        const key = `${message.taskId}:${message.dispatchId}`;
        const cached = this.resultCache.get(key);
        if (cached) cached.acked = true;
        return;
      }
      case "PROTOCOL_ERROR": {
        this.log(`PROTOCOL_ERROR 수신: ${message.code} ${message.message}`);
        return;
      }
      case "TASK": {
        this.handleTask(socket, message);
        return;
      }
      default:
        return;
    }
  }

  private startHeartbeat(socket: WebSocket): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => {
      if (socket.readyState !== WebSocket.OPEN) return;
      const heartbeat: Omit<HeartbeatMessage, "schemaVersion" | "eventId" | "sentAt"> = {
        type: "HEARTBEAT",
        deviceId: this.deviceId!,
        cpuPercent: collectSystemStatus().cpuPercent,
        memoryPercent: collectSystemStatus().memoryPercent,
        runningTaskId: null,
      };
      this.send(socket, heartbeat);
    }, this.options.heartbeatIntervalMs);
  }

  private resendUnackedResults(socket: WebSocket): void {
    for (const cached of this.resultCache.values()) {
      if (!cached.acked) this.send(socket, cached.result);
    }
  }

  private handleTask(socket: WebSocket, message: Extract<AgentMessage, { type: "TASK" }>): void {
    const key = `${message.taskId}:${message.dispatchId}`;
    const cached = this.resultCache.get(key);
    if (cached) {
      this.log(`중복 TASK 수신(${key}) — 재실행 없이 기존 결과 재전송`);
      this.send(socket, cached.ack);
      this.send(socket, cached.result);
      return;
    }

    const acknowledgedAt = nowIsoKst();
    const rejection = this.validateTask(message);
    const ack: Omit<AckMessage, "schemaVersion" | "eventId" | "sentAt"> = {
      type: "ACK",
      taskId: message.taskId,
      dispatchId: message.dispatchId,
      correlationId: message.correlationId,
      accepted: !rejection,
      reasonCode: rejection,
      acknowledgedAt,
    };
    this.send(socket, ack);
    if (rejection) {
      this.log(`TASK 거부(${message.taskType}): ${rejection}`);
      return;
    }

    const startedAt = nowIsoKst();
    const progress: Omit<ProgressMessage, "schemaVersion" | "eventId" | "sentAt"> = {
      type: "PROGRESS",
      taskId: message.taskId,
      dispatchId: message.dispatchId,
      correlationId: message.correlationId,
      stage: "EXECUTING",
      percent: 50,
    };
    this.send(socket, progress);

    const outcome = this.executeTask(message);
    const finishedAt = nowIsoKst();
    const result: Omit<ResultMessage, "schemaVersion" | "eventId" | "sentAt"> = {
      type: "RESULT",
      taskId: message.taskId,
      dispatchId: message.dispatchId,
      correlationId: message.correlationId,
      status: outcome.ok ? "SUCCEEDED" : "FAILED",
      result: outcome.ok ? outcome.result : null,
      error: outcome.ok ? null : outcome.error,
      startedAt,
      finishedAt,
    };
    this.resultCache.set(key, { ack, result, acked: false });
    this.send(socket, result);
  }

  private validateTask(message: Extract<AgentMessage, { type: "TASK" }>): AckMessage["reasonCode"] {
    if (!SUPPORTED_TASK_TYPES.includes(message.taskType)) return "TASK_TYPE_NOT_SUPPORTED";
    if (new Date(message.expiresAt).getTime() < Date.now()) return "TASK_EXPIRED";
    if (message.taskType === "FILE_SEARCH") {
      const query = message.parameters.query;
      const searchFolderId = message.parameters.searchFolderId;
      if (typeof query !== "string" || query.length === 0) return "INVALID_PARAMETERS";
      if (searchFolderId !== SEARCH_FOLDER_ID) return "SEARCH_FOLDER_NOT_FOUND";
    }
    if (message.taskType === "COMMAND") {
      const command = message.parameters.command;
      if (typeof command !== "string" || command.length === 0) return "INVALID_PARAMETERS";
    }
    return null;
  }

  private executeTask(
    message: Extract<AgentMessage, { type: "TASK" }>
  ):
    | { ok: true; result: Record<string, unknown> }
    | { ok: false; error: { code: ReasonCode; message: string; retryable: boolean } } {
    try {
      if (message.taskType === "SYSTEM_STATUS") {
        return { ok: true, result: { ...collectSystemStatus() } };
      }
      if (message.taskType === "FILE_SEARCH") {
        const query = String(message.parameters.query ?? "");
        return { ok: true, result: { ...searchFiles(this.options.searchFolderRoot, query) } };
      }
      if (message.taskType === "COMMAND") {
        // 백그라운드 명령 실행의 최소 구현 — 지금은 받은 명령을 그대로 에코한다.
        const command = String(message.parameters.command ?? "");
        return { ok: true, result: { output: command, executedAt: nowIsoKst() } };
      }
      return {
        ok: false,
        error: { code: "TASK_TYPE_NOT_SUPPORTED", message: "unsupported task type", retryable: false },
      };
    } catch (error) {
      return { ok: false, error: { code: "POLICY_DENIED", message: String(error), retryable: false } };
    }
  }
}
