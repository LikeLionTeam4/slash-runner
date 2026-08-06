import { describe, it, expect, afterEach } from "vitest";
import { randomUUID } from "node:crypto";
import { ContractAgent } from "../src/agent.js";
import { startFakeAgentServer, type FakeAgentServer } from "./fakeAgentServer.js";

/**
 * w1-06 대상 시험 — 재연결·중복 Task 단위 시험
 * - 실제 소켓·서명 검증을 쓰는 서버(fakeAgentServer)로 agent.ts를 직접 구동
 */

let server: FakeAgentServer | undefined;
let agent: ContractAgent | undefined;

afterEach(async () => {
  agent?.stop();
  agent = undefined;
  await server?.close();
  server = undefined;
});

async function startAgent(server: FakeAgentServer): Promise<ContractAgent> {
  const agent = new ContractAgent({
    apiBaseUrl: server.url,
    pairingCode: "000000",
    searchFolderRoot: process.cwd(),
    heartbeatIntervalMs: 60_000,
    log: () => {},
  });
  await agent.start();
  await agent.waitUntilReady();
  return agent;
}

describe("중복 TASK 처리", () => {
  it("같은 taskId·dispatchId를 두 번 받으면 재실행 없이 기존 결과를 재전송한다", async () => {
    server = await startFakeAgentServer();
    agent = await startAgent(server);

    const taskId = randomUUID();
    const dispatchId = randomUUID();
    server.sendTask({ taskId, dispatchId, taskType: "SYSTEM_STATUS", parameters: {} });
    const firstResult = await server.waitForMessage("RESULT");

    server.sendTask({ taskId, dispatchId, taskType: "SYSTEM_STATUS", parameters: {} });
    const secondResult = await server.waitForMessage("RESULT");

    // 재실행 시 finishedAt 갱신, 캐시 재전송 시 완전 동일
    expect(secondResult.finishedAt).toBe(firstResult.finishedAt);
    expect(secondResult.result).toEqual(firstResult.result);
  });
});

describe("재연결 시 미완료 결과 재전송", () => {
  it("RESULT_ACK를 못 받은 상태에서 재연결하면 같은 RESULT를 다시 보낸다", async () => {
    server = await startFakeAgentServer();
    server.autoAckResult = false; // RESULT_ACK를 보내지 않아 "미완료" 상태를 만든다
    agent = await startAgent(server);

    server.sendTask({ taskId: randomUUID(), dispatchId: randomUUID(), taskType: "SYSTEM_STATUS", parameters: {} });
    await server.waitForMessage("RESULT");

    const sinceIndex = server.receivedMessages.length;
    server.disconnectAgent();

    const resent = await server.waitForMessage("RESULT", 10_000, sinceIndex);
    expect(resent).toBeTruthy();
  }, 15_000);

  it("RESULT_ACK를 받은 뒤에는 재연결해도 다시 보내지 않는다", async () => {
    server = await startFakeAgentServer();
    server.autoAckResult = true;
    agent = await startAgent(server);

    server.sendTask({ taskId: randomUUID(), dispatchId: randomUUID(), taskType: "SYSTEM_STATUS", parameters: {} });
    await server.waitForMessage("RESULT");
    // RESULT_ACK: 서버 발신 메시지라 관측 불가 — 처리 대기 시간만 부여
    await new Promise((resolve) => setTimeout(resolve, 200));

    const readyBefore = server.readyCount;
    const sinceIndex = server.receivedMessages.length;
    server.disconnectAgent();

    // 재연결(READY 재도달) 선행 확인 — "재전송 안 함" 검증의 전제조건
    await server.waitForMessage("HELLO", 10_000, sinceIndex);
    await new Promise<void>((resolve, reject) => {
      const timer = setInterval(() => {
        if (server!.readyCount > readyBefore) {
          clearInterval(timer);
          resolve();
        }
      }, 100);
      setTimeout(() => {
        clearInterval(timer);
        reject(new Error("재연결 시간 초과"));
      }, 10_000);
    });

    await expect(server.waitForMessage("RESULT", 2000, sinceIndex)).rejects.toThrow();
  }, 20_000);
});
