import { fileURLToPath } from "node:url";
import { dirname, resolve, join } from "node:path";
import { readFileSync, writeFileSync, existsSync, mkdirSync, unlinkSync } from "node:fs";
import { homedir } from "node:os";
import { ContractAgent } from "./agent.js";
import type { AgentIdentityStore } from "./agentIdentityStore.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * CLI는 Electron이 아니라 OS 키체인 API(safeStorage)를 쓸 수 없다 — 그래서 개발용 시뮬레이터
 * 전용으로 평문 JSON 파일에 저장한다. 실제 배포 대상인 agent-app(Electron)은 safeStorage로
 * macOS Keychain/Windows Credential Manager에 저장한다(메시지 프로토콜 문서 §8.1 3단계 요구사항은
 * 그쪽에서 충족된다). 이 파일은 재페어링 없이 재시작하기 위한 개발 편의 용도일 뿐이다.
 */
function createDevIdentityStore(): AgentIdentityStore {
  const dir = join(homedir(), ".slash-contract-agent");
  const path = join(dir, "identity.json");
  return {
    async load() {
      if (!existsSync(path)) return null;
      try {
        return JSON.parse(readFileSync(path, "utf8"));
      } catch {
        return null;
      }
    },
    async save(identity) {
      mkdirSync(dir, { recursive: true });
      writeFileSync(path, JSON.stringify(identity, null, 2), { mode: 0o600 });
    },
    async clear() {
      if (existsSync(path)) unlinkSync(path);
    },
  };
}

async function obtainPairingCode(apiBaseUrl: string): Promise<string> {
  const explicit = process.env.CONTRACT_AGENT_PAIRING_CODE;
  if (explicit) return explicit;

  const loginRes = await fetch(`${apiBaseUrl}/test/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "contract-agent-tester@example.com", displayName: "contract-agent tester" }),
  });
  const login = (await loginRes.json()) as { token: string };

  const pairingRes = await fetch(`${apiBaseUrl}/api/v1/pairing-requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${login.token}` },
  });
  // /api/v1/** 는 {data,meta} 봉투를 쓴다 (메시지 프로토콜 문서 §3.3).
  const pairing = (await pairingRes.json()) as { data: { pairingCode: string } };
  console.log(`[contract-agent] 자동 발급된 페어링 코드: ${pairing.data.pairingCode}`);
  return pairing.data.pairingCode;
}

async function main(): Promise<void> {
  const apiBaseUrl = process.env.CONTRACT_AGENT_API_BASE_URL ?? "http://localhost:4000";
  const searchFolderRoot = resolve(__dirname, "../../fixtures/search-folder");
  const identityStore = createDevIdentityStore();
  const hasPersistedIdentity = (await identityStore.load()) !== null;
  // 저장된 기기 식별 정보가 있으면 매 실행마다 페어링 코드를 새로 받지 않는다 — agent.ts가
  // 재페어링 대신 토큰 갱신을 먼저 시도한다.
  const pairingCode = hasPersistedIdentity ? undefined : await obtainPairingCode(apiBaseUrl);

  const agent = new ContractAgent({
    apiBaseUrl,
    pairingCode,
    searchFolderRoot,
    deviceName: process.env.CONTRACT_AGENT_DEVICE_NAME ?? "contract-agent-simulator",
    heartbeatIntervalMs: Number(process.env.CONTRACT_AGENT_HEARTBEAT_INTERVAL_MS ?? 30_000),
    log: (line) => console.log(line),
    identityStore,
  });

  await agent.start();
  await agent.waitUntilReady(20_000);
  console.log(`[contract-agent] READY (deviceId=${agent.getDeviceId()})`);

  process.on("SIGINT", () => {
    agent.stop();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    agent.stop();
    process.exit(0);
  });
}

main().catch((error) => {
  console.error("[contract-agent] 시작 실패:", error);
  process.exit(1);
});
