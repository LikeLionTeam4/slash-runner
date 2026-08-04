// Slash 로컬 에이전트 macOS 메뉴바 앱.
// 실제 Agent WSS 로직은 전부 @slash-agent/contract-agent(ContractAgent)를 그대로 재사용한다 —
// 이 파일은 그 위에 "메뉴바 아이콘 + 종료 메뉴" 셸을 씌우는 것뿐이다.
//
// CommonJS로 작성한 이유: Electron 메인 프로세스가 ESM(`import`)일 때 `electron` 모듈의
// 특수 바인딩(자동 API 주입)이 일부 버전에서 제대로 안 걸려 `require('electron')`이 반환하는
// "바이너리 경로 문자열"만 넘어오는 문제를 실제로 겪었다 — CJS `require('electron')`이
// 훨씬 안정적으로 검증된 경로라 이쪽을 쓴다.
//
// ContractAgent는 npm workspace 패키지명(`@slash-agent/contract-agent`)이 아니라
// `vendor/contract-agent.mjs`(esbuild로 미리 번들된 단일 파일, `npm run build:vendor`가 생성)를
// 상대경로로 직접 불러온다 — workspace 심볼릭 링크에 의존하면 electron-builder가 패키징할 때
// 그 링크가 원본 저장소 경로를 벗어나지 못해 "빌드된 .app만 다른 곳으로 옮기면 깨지는" 문제가
// 생긴다. 번들을 이 앱 안에 실제 파일로 넣어두면 그 문제가 없다. 순수 ESM 파일이라 동적
// `import()`로 불러온다(CJS에서 ESM을 부르는 건 Node가 공식 지원한다).
const { app, Tray, Menu, nativeImage, shell } = require("electron");
const { readFileSync, writeFileSync, existsSync, mkdirSync } = require("node:fs");
const { join } = require("node:path");
const { pathToFileURL } = require("node:url");
const { homedir } = require("node:os");

// 메뉴바 전용 앱 — Dock 아이콘/창 없음 (package.json의 LSUIElement와 짝).
app.dock?.hide();

const CONFIG_DIR = join(homedir(), "Library", "Application Support", "slash-agent-app");
const CONFIG_PATH = join(CONFIG_DIR, "config.json");
const CONFIG_EXAMPLE_PATH = join(CONFIG_DIR, "config.example.json");

/**
 * Finder에서 더블클릭으로 켠 앱은 터미널 환경변수를 물려받지 않는다. 그래서 설정은
 * (1) ~/Library/Application Support/slash-agent-app/config.json → (2) 환경변수 → (3) 기본값
 * 순서로 읽는다. CLI(`contract-agent/src/cli.ts`)와 동일한 기본값을 쓴다.
 */
function loadConfig() {
  let fileConfig = {};
  if (existsSync(CONFIG_PATH)) {
    try {
      fileConfig = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
    } catch (error) {
      console.error("config.json 파싱 실패, 무시하고 진행:", error);
    }
  }
  const apiBaseUrl = fileConfig.apiBaseUrl ?? process.env.CONTRACT_AGENT_API_BASE_URL ?? "http://localhost:4000";
  const pairingCode = fileConfig.pairingCode ?? process.env.CONTRACT_AGENT_PAIRING_CODE ?? null;
  const deviceName = fileConfig.deviceName ?? process.env.CONTRACT_AGENT_DEVICE_NAME ?? "slash-agent-app (macOS)";
  const heartbeatIntervalMs = Number(
    fileConfig.heartbeatIntervalMs ?? process.env.CONTRACT_AGENT_HEARTBEAT_INTERVAL_MS ?? 30_000
  );
  return { apiBaseUrl, pairingCode, deviceName, heartbeatIntervalMs };
}

function resolveSearchFolderRoot() {
  // 패키징된 앱: electron-builder가 fixtures/search-folder를 Resources 안에 복사해 둔다.
  // 개발 중(`npm run start`, 언패키지): 이 저장소 안의 fixtures/search-folder를 그대로 쓴다.
  if (app.isPackaged) {
    return join(process.resourcesPath, "search-folder");
  }
  return join(__dirname, "../../fixtures/search-folder");
}

/** 등록 코드가 없으면 시험 전용 자동 로그인+등록코드 발급으로 채운다 (cli.ts와 동일한 편의 로직). */
async function obtainPairingCode(apiBaseUrl) {
  const loginRes = await fetch(`${apiBaseUrl}/test/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "agent-app-tester@example.com", displayName: "agent-app tester" }),
  });
  const login = await loginRes.json();
  const pairingRes = await fetch(`${apiBaseUrl}/api/v1/pairing-requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${login.token}` },
  });
  // /api/v1/** 는 {data,meta} 봉투를 쓴다 (메시지 프로토콜 문서 §3.3).
  const pairing = await pairingRes.json();
  return pairing.data.pairingCode;
}

const STATE_LABEL = {
  CONNECTING: "연결 중...",
  AUTHENTICATING: "인증 중...",
  READY: "READY",
  OFFLINE: "오프라인 (재연결 시도 중)",
  STOPPED: "중지됨",
};

let tray = null;
let agent = null;
let currentConfig = null;

function trayIconImage() {
  const iconPath = join(__dirname, "../assets/trayIcon.png");
  // 브랜드 그라디언트 아이콘이라 template(흑백 강제) 모드는 쓰지 않는다 — 색이 사라진다.
  return nativeImage.createFromPath(iconPath);
}

function buildMenu() {
  const state = agent?.getState() ?? "CONNECTING";
  const deviceId = agent?.getDeviceId();
  return Menu.buildFromTemplate([
    { label: "Slash Agent", enabled: false },
    { type: "separator" },
    { label: `상태: ${STATE_LABEL[state] ?? state}`, enabled: false },
    { label: `기기 ID: ${deviceId ? deviceId.slice(0, 8) + "…" : "-"}`, enabled: false },
    { label: `mock-api: ${currentConfig?.apiBaseUrl ?? "-"}`, enabled: false },
    { type: "separator" },
    {
      label: "설정 폴더 열기",
      click: () => {
        shell.showItemInFolder(CONFIG_PATH);
      },
    },
    { type: "separator" },
    { label: "종료", click: () => app.quit() },
  ]);
}

function refreshMenu() {
  if (tray) tray.setContextMenu(buildMenu());
}

async function startAgent() {
  const vendorPath = join(__dirname, "../vendor/contract-agent.mjs");
  const { ContractAgent } = await import(pathToFileURL(vendorPath).href);

  currentConfig = loadConfig();
  const pairingCode = currentConfig.pairingCode ?? (await obtainPairingCode(currentConfig.apiBaseUrl));

  agent = new ContractAgent({
    apiBaseUrl: currentConfig.apiBaseUrl,
    pairingCode,
    searchFolderRoot: resolveSearchFolderRoot(),
    deviceName: currentConfig.deviceName,
    heartbeatIntervalMs: currentConfig.heartbeatIntervalMs,
    log: (line) => console.log(line),
  });

  await agent.start();
  refreshMenu();
  agent.waitUntilReady(20_000).then(refreshMenu, () => refreshMenu());
}

app.whenReady().then(async () => {
  mkdirSync(CONFIG_DIR, { recursive: true });
  if (!existsSync(CONFIG_EXAMPLE_PATH)) {
    writeFileSync(
      CONFIG_EXAMPLE_PATH,
      JSON.stringify(
        {
          apiBaseUrl: "http://localhost:4000",
          pairingCode: "123456",
          deviceName: "내 Mac",
          heartbeatIntervalMs: 30000,
        },
        null,
        2
      )
    );
  }

  tray = new Tray(trayIconImage());
  tray.setToolTip("Slash Agent");
  tray.setContextMenu(buildMenu());

  setInterval(refreshMenu, 2000);

  try {
    await startAgent();
  } catch (error) {
    console.error("에이전트 시작 실패:", error);
    tray.setToolTip(`Slash Agent — 시작 실패: ${String(error)}`);
  }
});

app.on("window-all-closed", (event) => {
  // 메뉴바 앱은 창이 없으니 창이 전부 닫혀도(=항상) 종료하지 않는다.
  event.preventDefault();
});

app.on("before-quit", () => {
  agent?.stop();
});
