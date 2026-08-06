import { createServer, type Server as HttpServer } from "node:http";
import { WebSocketServer, WebSocket } from "ws";
import { randomUUID, createPublicKey, verify as cryptoVerify } from "node:crypto";
import {
  AgentMessage,
  agentMessageSchema,
  AGENT_SCHEMA_VERSION,
  buildChallengeSigningPayload,
  buildRefreshSigningPayload,
  nowIsoKst,
} from "@slash-agent/contracts";

/**
 * mock-api의 Agent 페어링·WSS 취급을 최소 재현한 시험 전용 서버
 * - 목적: agent.ts의 재연결·중복방지·토큰 갱신 흐름을 실제 네트워크·서명 검증으로 시험
 * - 범위: 시험에 필요한 최소 라우트만, 실제 mock-api와 무관한 독립 구현
 */

function publicKeyFromBase64Raw(publicKeyBase64: string) {
  const raw = Buffer.from(publicKeyBase64, "base64");
  const jwk = { kty: "OKP", crv: "Ed25519", x: raw.toString("base64url") };
  return createPublicKey({ key: jwk, format: "jwk" });
}

function verifySignature(payload: string, signatureBase64: string, publicKeyBase64: string): boolean {
  try {
    return cryptoVerify(null, Buffer.from(payload), publicKeyFromBase64Raw(publicKeyBase64), Buffer.from(signatureBase64, "base64"));
  } catch {
    return false;
  }
}

interface DeviceRecord {
  deviceId: string;
  publicKeyBase64: string;
  deviceToken: string;
  revoked: boolean;
}

interface PairingSession {
  pairingSessionId: string;
  deviceId: string;
  challengeId: string;
  nonce: string;
  publicKeyBase64: string;
}

export interface FakeAgentServer {
  url: string;
  devices: Map<string, DeviceRecord>;
  /** TASK 프레임 전송 — 마지막 READY 연결 대상 */
  sendTask(params: { taskId: string; dispatchId: string; taskType: string; parameters: Record<string, unknown> }): void;
  /** RESULT_ACK 자동 응답 여부(기본 true) */
  autoAckResult: boolean;
  /** 소켓 강제 종료 — 재연결 시나리오 유발용 */
  disconnectAgent(): void;
  /** sinceIndex 이후 기수신 메시지 즉시 반환, 없으면 신규 대기 */
  waitForMessage<T extends AgentMessage["type"]>(
    type: T,
    timeoutMs?: number,
    sinceIndex?: number
  ): Promise<Extract<AgentMessage, { type: T }>>;
  /** 전체 수신 메시지 로그 */
  receivedMessages: AgentMessage[];
  /** READY 도달 횟수 — 재연결 여부 확인용. */
  readyCount: number;
  close(): Promise<void>;
}

export async function startFakeAgentServer(): Promise<FakeAgentServer> {
  const devices = new Map<string, DeviceRecord>();
  const pairingSessions = new Map<string, PairingSession>();
  const receivedMessages: AgentMessage[] = [];
  const waiters: Array<{ type: string; resolve: (message: AgentMessage) => void }> = [];
  let currentSocket: WebSocket | null = null;
  let currentDeviceId: string | null = null;
  let readyCount = 0;
  const state = { autoAckResult: true };

  function notifyWaiters(message: AgentMessage): void {
    const idx = waiters.findIndex((w) => w.type === message.type);
    if (idx >= 0) {
      const [waiter] = waiters.splice(idx, 1);
      waiter.resolve(message);
    }
  }

  function send(socket: WebSocket, message: Omit<AgentMessage, "schemaVersion" | "eventId" | "sentAt">): void {
    const full = { schemaVersion: AGENT_SCHEMA_VERSION, eventId: randomUUID(), sentAt: nowIsoKst(), ...message } as AgentMessage;
    socket.send(JSON.stringify(full));
  }

  const httpServer: HttpServer = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const body = chunks.length > 0 ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
      handleHttp(req.url ?? "", body, res);
    });
  });

  function handleHttp(url: string, body: any, res: import("node:http").ServerResponse): void {
    const respond = (status: number, data: unknown) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ data }));
    };
    const fail = (status: number, code: string, message: string) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: { code, message } }));
    };

    if (url === "/api/v1/agent/pair") {
      const deviceId = randomUUID();
      devices.set(deviceId, { deviceId, publicKeyBase64: body.publicKey, deviceToken: "", revoked: false });
      const pairingSessionId = randomUUID();
      const challengeId = randomUUID();
      const nonce = Buffer.from(randomUUID()).toString("base64");
      pairingSessions.set(pairingSessionId, { pairingSessionId, deviceId, challengeId, nonce, publicKeyBase64: body.publicKey });
      respond(201, { pairingSessionId, deviceId, challengeId, nonce, expiresAt: nowIsoKst() });
      return;
    }

    if (url === "/api/v1/agent/pair/verify") {
      const session = pairingSessions.get(body.pairingSessionId);
      if (!session || session.challengeId !== body.challengeId) return fail(404, "RESOURCE_NOT_FOUND", "세션 없음");
      const valid = verifySignature(
        buildChallengeSigningPayload({ challengeId: session.challengeId, nonce: session.nonce, deviceId: session.deviceId }),
        body.signature,
        session.publicKeyBase64
      );
      if (!valid) return fail(422, "AGENT_AUTH_FAILED", "서명 검증 실패");
      const device = devices.get(session.deviceId)!;
      device.deviceToken = randomUUID();
      respond(200, { deviceToken: device.deviceToken, expiresIn: 86_400, issuedAt: nowIsoKst(), wsUrl: "" });
      return;
    }

    if (url === "/api/v1/agent/sessions/refresh") {
      const device = devices.get(body.deviceId);
      if (!device) return fail(401, "AUTH_REQUIRED", "미등록 기기");
      if (device.revoked) return fail(409, "FORBIDDEN", "등록 해제된 기기");
      const valid = verifySignature(
        buildRefreshSigningPayload({ deviceId: body.deviceId, refreshNonce: body.refreshNonce, requestedAt: body.requestedAt }),
        body.signature,
        device.publicKeyBase64
      );
      if (!valid) return fail(403, "AGENT_AUTH_FAILED", "서명 검증 실패");
      device.deviceToken = randomUUID();
      respond(200, { deviceToken: device.deviceToken, expiresIn: 86_400, issuedAt: nowIsoKst() });
      return;
    }

    fail(404, "NOT_FOUND", "알 수 없는 경로");
  }

  const wss = new WebSocketServer({ server: httpServer, path: "/ws/agent" });
  wss.on("connection", (socket) => {
    currentSocket = socket;
    let helloDeviceId: string | null = null;

    socket.on("message", (raw) => {
      const parsed = agentMessageSchema.safeParse(JSON.parse(raw.toString()));
      if (!parsed.success) return;
      const message = parsed.data;
      receivedMessages.push(message);
      notifyWaiters(message);

      if (message.type === "HELLO") {
        helloDeviceId = message.deviceId;
        currentDeviceId = message.deviceId;
        const challengeId = randomUUID();
        const nonce = Buffer.from(randomUUID()).toString("base64");
        (socket as any)._challenge = { challengeId, nonce };
        send(socket, { type: "CHALLENGE", challengeId, nonce, expiresAt: nowIsoKst() });
        return;
      }
      if (message.type === "AUTH") {
        const device = devices.get(helloDeviceId!);
        const challenge = (socket as any)._challenge;
        const valid =
          device &&
          challenge &&
          verifySignature(
            buildChallengeSigningPayload({ challengeId: challenge.challengeId, nonce: challenge.nonce, deviceId: helloDeviceId! }),
            message.signature,
            device.publicKeyBase64
          );
        if (!valid) return;
        send(socket, {
          type: "READY",
          maxConcurrentTasks: 1,
          supportedTaskTypes: ["FILE_SEARCH", "SYSTEM_STATUS"],
          searchFolders: [],
          projectWorkspaces: [],
        });
        readyCount += 1;
        return;
      }
      if (message.type === "RESULT" && state.autoAckResult) {
        send(socket, { type: "RESULT_ACK", taskId: message.taskId, dispatchId: message.dispatchId, correlationId: message.correlationId, persisted: true, taskStatus: "SUCCEEDED" });
      }
    });

    socket.on("close", () => {
      if (currentSocket === socket) currentSocket = null;
    });
  });

  await new Promise<void>((resolve) => httpServer.listen(0, resolve));
  const port = (httpServer.address() as { port: number }).port;

  return {
    url: `http://localhost:${port}`,
    devices,
    get autoAckResult() {
      return state.autoAckResult;
    },
    set autoAckResult(value: boolean) {
      state.autoAckResult = value;
    },
    sendTask({ taskId, dispatchId, taskType, parameters }) {
      if (!currentSocket) throw new Error("연결된 에이전트 소켓이 없습니다");
      send(currentSocket, {
        type: "TASK",
        taskId,
        dispatchId,
        correlationId: randomUUID(),
        taskType: taskType as any,
        parameters,
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
        payloadSha256: "0".repeat(64),
      });
    },
    disconnectAgent() {
      currentSocket?.close();
    },
    waitForMessage(type, timeoutMs = 5000, sinceIndex = 0) {
      const existing = receivedMessages.slice(sinceIndex).find((m) => m.type === type);
      if (existing) return Promise.resolve(existing as any);
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${type} 대기 시간 초과`)), timeoutMs);
        waiters.push({
          type,
          resolve: (message) => {
            clearTimeout(timer);
            resolve(message as any);
          },
        });
      });
    },
    receivedMessages,
    get readyCount() {
      return readyCount;
    },
    async close() {
      await new Promise<void>((resolve) => wss.close(() => resolve()));
      await new Promise<void>((resolve) => httpServer.close(() => resolve()));
    },
  };
}
