import {
  AgentPairRequest,
  AgentPairResponse,
  AgentPairVerifyRequest,
  AgentPairVerifyResponse,
  AgentSessionRefreshRequest,
  AgentSessionRefreshResponse,
  TaskType,
  DeviceOs,
} from "@slash-agent/contracts";

export interface PairAgentParams {
  apiBaseUrl: string;
  pairingCode: string;
  publicKeyBase64: string;
  deviceName: string;
  supportedTaskTypes: TaskType[];
}

/** 페어링 REST 응답은 {data,meta} 봉투를 쓴다 (메시지 프로토콜 문서 §3.3). */
async function postJson<T>(url: string, body: unknown, headers?: Record<string, string>): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  const parsed = (await res.json().catch(() => null)) as { data: T } | { error: { code: string; message: string } } | null;
  if (!res.ok) {
    const message = parsed && "error" in parsed ? `${parsed.error.code}: ${parsed.error.message}` : `HTTP ${res.status}`;
    throw new Error(`POST ${url} failed: ${message}`);
  }
  return (parsed as { data: T }).data;
}

export async function pairAgent(params: PairAgentParams): Promise<AgentPairResponse> {
  const body: AgentPairRequest = {
    pairingCode: params.pairingCode,
    publicKey: params.publicKeyBase64,
    device: {
      name: params.deviceName,
      os: "MACOS" as DeviceOs,
      architecture: process.arch === "arm64" ? "ARM64" : "X86_64",
      osVersion: process.platform,
      agentVersion: "contract-agent-simulator/0.1.0",
    },
    supportedTaskTypes: params.supportedTaskTypes,
  };
  return postJson<AgentPairResponse>(`${params.apiBaseUrl}/api/v1/agent/pair`, body);
}

export async function verifyPairing(
  apiBaseUrl: string,
  body: AgentPairVerifyRequest
): Promise<AgentPairVerifyResponse> {
  return postJson<AgentPairVerifyResponse>(`${apiBaseUrl}/api/v1/agent/pair/verify`, body);
}

/** 재페어링 없이 기기 인증 토큰만 갱신한다 (메시지 프로토콜 문서 §8.1 3단계). */
export async function refreshSession(
  apiBaseUrl: string,
  currentDeviceToken: string,
  body: AgentSessionRefreshRequest
): Promise<AgentSessionRefreshResponse> {
  return postJson<AgentSessionRefreshResponse>(`${apiBaseUrl}/api/v1/agent/sessions/refresh`, body, {
    Authorization: `Bearer ${currentDeviceToken}`,
  });
}
