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
  buildRefreshSigningPayload,
  nowIsoKst,
} from "@slash-agent/contracts";
import { AgentKeyPair, generateAgentKeyPair, restoreAgentKeyPair, exportPrivateKeyPem, signPayload } from "./agentCrypto.js";
import { pairAgent, verifyPairing, refreshSession } from "./pairingClient.js";
import { AgentIdentityStore } from "./agentIdentityStore.js";
import { collectSystemStatus } from "./systemStatus.js";
import { searchFiles } from "./fileSearch.js";
import { randomUUID } from "node:crypto";

export const SEARCH_FOLDER_ID = "sf-fixtures-01";
export const SUPPORTED_TASK_TYPES: TaskType[] = ["FILE_SEARCH", "SYSTEM_STATUS"];

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
  /**
   * 기기 식별 정보(개인키·deviceId·deviceToken) 영속화 저장소. 주어지면 시작 시 저장된 값을
   * 우선 불러와 재페어링 대신 토큰 갱신(§8.1 3단계)을 시도하고, 페어링·갱신에 성공할 때마다
   * 최신 값을 다시 저장한다. 생략하면 매 실행마다 새 키로 재페어링한다(기존 동작 유지).
   */
  identityStore?: AgentIdentityStore;
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
  identityStore?: AgentIdentityStore;
}

export class ContractAgent {
  private readonly options: ResolvedContractAgentOptions;
  private keyPair: AgentKeyPair = generateAgentKeyPair();
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
      identityStore: options.identityStore,
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
    await this.loadPersistedIdentity();
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

  /** 저장소에 남아있는 기기 식별 정보를 불러온다. preset이 이미 주어졌다면 그쪽을 우선한다. */
  private async loadPersistedIdentity(): Promise<void> {
    if (this.deviceToken || !this.options.identityStore) return;
    const persisted = await this.options.identityStore.load();
    if (!persisted) return;
    this.keyPair = restoreAgentKeyPair(persisted.privateKeyPem, persisted.publicKeyBase64);
    this.deviceId = persisted.deviceId;
    this.deviceToken = persisted.deviceToken;
    this.log(`저장된 기기 ID를 불러왔습니다 deviceId=${this.deviceId}`);
  }

  private async persistIdentity(): Promise<void> {
    if (!this.options.identityStore || !this.deviceId || !this.deviceToken) return;
    await this.options.identityStore.save({
      deviceId: this.deviceId,
      deviceToken: this.deviceToken,
      privateKeyPem: exportPrivateKeyPem(this.keyPair.privateKey),
      publicKeyBase64: this.keyPair.publicKeyBase64,
    });
  }

  /**
   * 이미 등록된 기기라면 재페어링 대신 토큰 갱신을 시도한다(메시지 프로토콜 문서 §8.1 3단계).
   * 서버가 갱신 엔드포인트를 아직 지원하지 않거나(404), 기기가 등록 해제됐거나, 서명 검증에
   * 실패하면 false를 반환해 호출부가 재페어링으로 넘어가게 한다.
   */
  private async tryRefreshSession(): Promise<boolean> {
    if (!this.deviceId || !this.deviceToken) return false;
    try {
      const refreshNonce = randomUUID();
      const requestedAt = nowIsoKst();
      const signature = signPayload(
        this.keyPair.privateKey,
        buildRefreshSigningPayload({ deviceId: this.deviceId, refreshNonce, requestedAt })
      );
      const response = await refreshSession(this.options.apiBaseUrl, this.deviceToken, {
        deviceId: this.deviceId,
        refreshNonce,
        requestedAt,
        signature,
      });
      this.deviceToken = response.deviceToken;
      this.log(`기기 인증 토큰을 갱신했습니다 deviceId=${this.deviceId}`);
      await this.persistIdentity();
      return true;
    } catch (error) {
      this.log(`토큰 갱신 실패, 재페어링으로 전환합니다: ${String(error)}`);
      return false;
    }
  }

  private async pairIfNeeded(): Promise<void> {
    if (this.deviceToken && this.deviceId) {
      if (await this.tryRefreshSession()) return;
      this.deviceId = null;
      this.deviceToken = null;
      await this.options.identityStore?.clear();
    }
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
    await this.persistIdentity();
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
      return {
        ok: false,
        error: { code: "TASK_TYPE_NOT_SUPPORTED", message: "unsupported task type", retryable: false },
      };
    } catch (error) {
      return { ok: false, error: { code: "POLICY_DENIED", message: String(error), retryable: false } };
    }
  }
}
