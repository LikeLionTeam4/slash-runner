import { describe, it, expect, afterEach } from "vitest";
import { ContractAgent } from "../src/agent.js";
import type { AgentIdentityStore, PersistedAgentIdentity } from "../src/agentIdentityStore.js";
import { generateAgentKeyPair, exportPrivateKeyPem } from "../src/agentCrypto.js";
import { startFakeAgentServer, type FakeAgentServer } from "./fakeAgentServer.js";

/**
 * w1-07 대상 시험 — 등록(페어링) 실패·재페어링 폴백 경로
 * - 정상 페어링·재연결·중복방지는 agent.reconnect-dedupe.test.ts에서 다룸
 */

let server: FakeAgentServer | undefined;
let agent: ContractAgent | undefined;

afterEach(async () => {
  agent?.stop();
  agent = undefined;
  await server?.close();
  server = undefined;
});

function memoryIdentityStore(initial: PersistedAgentIdentity | null): AgentIdentityStore & { saved: PersistedAgentIdentity[] } {
  let current = initial;
  const saved: PersistedAgentIdentity[] = [];
  return {
    saved,
    async load() {
      return current;
    },
    async save(identity) {
      current = identity;
      saved.push(identity);
    },
    async clear() {
      current = null;
    },
  };
}

describe("페어링 실패", () => {
  it("등록 코드가 틀리면 명확한 오류로 시작에 실패한다", async () => {
    server = await startFakeAgentServer();
    server.acceptedPairingCode = "111111";

    agent = new ContractAgent({
      apiBaseUrl: server.url,
      pairingCode: "000000", // 서버가 받아주지 않는 코드
      searchFolderRoot: process.cwd(),
      heartbeatIntervalMs: 60_000,
      log: () => {},
    });

    await expect(agent.start()).rejects.toThrow(/PAIRING_CODE_INVALID/);
  });
});

describe("토큰 갱신 실패 후 재페어링 폴백", () => {
  it("저장된 기기가 서버에 없으면(미등록) 재페어링으로 복구하고 새 식별자를 저장한다", async () => {
    server = await startFakeAgentServer();
    server.acceptedPairingCode = "222222";

    // 서버 미인지 deviceId·deviceToken 사전 저장 상태 재현 — 키는 실제 유효한 Ed25519
    const staleKeyPair = generateAgentKeyPair();
    const identityStore = memoryIdentityStore({
      deviceId: "stale-device-id",
      deviceToken: "stale-token",
      privateKeyPem: exportPrivateKeyPem(staleKeyPair.privateKey),
      publicKeyBase64: staleKeyPair.publicKeyBase64,
    });

    agent = new ContractAgent({
      apiBaseUrl: server.url,
      pairingCode: "222222", // 갱신 실패 시 폴백용
      searchFolderRoot: process.cwd(),
      heartbeatIntervalMs: 60_000,
      log: () => {},
      identityStore,
    });

    await agent.start();
    await agent.waitUntilReady();

    expect(agent.getDeviceId()).not.toBe("stale-device-id");
    expect(identityStore.saved.length).toBeGreaterThan(0);
    expect(identityStore.saved.at(-1)!.deviceId).toBe(agent.getDeviceId());
  });
});
