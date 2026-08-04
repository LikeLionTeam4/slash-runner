import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { ContractAgent } from "./agent.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

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
  const pairingCode = await obtainPairingCode(apiBaseUrl);

  const agent = new ContractAgent({
    apiBaseUrl,
    pairingCode,
    searchFolderRoot,
    deviceName: process.env.CONTRACT_AGENT_DEVICE_NAME ?? "contract-agent-simulator",
    heartbeatIntervalMs: Number(process.env.CONTRACT_AGENT_HEARTBEAT_INTERVAL_MS ?? 30_000),
    log: (line) => console.log(line),
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
