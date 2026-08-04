# Slash | 로컬 에이전트

Slash(/)는 자연어 질문과 `/` 슬래시 명령어를 한 입력창에서 처리하는 AI 에이전트 서비스입니다.
이 저장소는 그중 **사용자 PC에서 동작하는 로컬 에이전트** 파트를 담당합니다.

## 역할

- 사용자 PC 파일 검색
- 상태 조회
- 로컬 AI 에이전트 실행 및 결과 전달

## 구성

```text
contracts/        공유 타입·zod 스키마 (REST/Agent WSS 계약의 단일 소스)
contract-agent/   핵심 로직 — WebSocket 클라이언트, Ed25519 인증, 작업 실행. GUI 없는 순수 라이브러리 + CLI.
agent-app/        contract-agent를 감싼 macOS 메뉴바 실행파일 (Electron 셸)
fixtures/         FILE_SEARCH 데모용 샘플 폴더 (개발 중 실행·패키징된 앱 모두 여기를 기본 검색 폴더로 쓴다)
docs/             메시지 실전 예시 가이드 (docs/MESSAGE_GUIDE.md)
legacy-macos/     (이 저장소에는 포함되지 않음, .gitignore 처리) — 참고용 구 목업, 로컬에만 존재
```

`agent-app`은 `contract-agent`의 로직을 **그대로 재사용**한다 — GUI는 그 위에 얹은 얇은 껍데기일 뿐,
WebSocket 프로토콜·인증·작업 실행 코드는 한 곳(`contract-agent/src/`)에만 존재한다.

## 빌드해서 실행파일 만들기

```bash
cd agent-app
npm install
npm run dist:mac
```

- 결과물: `agent-app/dist/mac-arm64/Slash Agent.app`
- `npm run dist:mac`은 먼저 `contract-agent`를 esbuild로 단일 파일(`agent-app/vendor/contract-agent.mjs`)로
  번들링한 뒤(`predist:mac` 훅), Electron으로 `.app`을 패키징한다.
- **Apple Silicon(arm64) 전용**으로 빌드한다(`electron-builder --mac --arm64`). Intel Mac 빌드는 아직
  시험하지 않았다.
- Apple Developer ID로 서명하지 않은 상태다(무료 개인 사용 목적). macOS는 arm64 실행파일에 최소한 **ad-hoc
  서명**은 요구하는데, electron-builder가 별도 인증서를 못 찾으면 보통 자동으로 ad-hoc 서명(`codesign -s -`)을
  한다. 만약 처음 실행 시 "확인되지 않은 개발자" 경고가 뜨면:
  ```bash
  xattr -cr "agent-app/dist/mac-arm64/Slash Agent.app"   # 격리 속성 제거
  # 그래도 안 되면 수동 ad-hoc 서명
  codesign --deep --force --sign - "agent-app/dist/mac-arm64/Slash Agent.app"
  ```
  Finder에서는 더블클릭 대신 **우클릭 → 열기**로 처음 한 번 실행하면 이 경고를 건너뛸 수 있다.
- `package.json`의 `build.npmRebuild`는 반드시 `false`여야 한다. electron-builder는 기본적으로 패키징
  전에 "production dependencies 재설치" 단계를 도는데, 이 저장소가 npm workspaces 구조라 그 단계가
  루트에 hoisting된 devDependencies(`app-builder-bin` 포함)를 지워버려서 **electron-builder가 자기
  자신의 의존성을 삭제해 빌드 도중 `spawn .../app-builder-bin/mac/app-builder_arm64 ENOENT`로 죽는다.**
  이 앱의 런타임 의존성(`ws`, `zod`)은 순수 JS라 애초에 이 단계 자체가 필요 없다 — 이 옵션을 실수로
  지우면 다시 겪는다.

**로컬에 Node.js/Python이 설치돼 있는지와 무관하게 실행되는지?** — 그렇다. Electron은 자체 Node.js·Chromium
런타임을 앱 번들 안에 통째로 포함한다. `.app`을 더블클릭해서 실행하는 순간부터는 시스템에 별도로 Node나
Python이 깔려 있을 필요가 없다(빌드할 때는 당연히 Node/npm이 필요하지만, 빌드된 결과물 자체는 독립적이다).

## 개발 중 실행 (패키징 없이)

```bash
cd agent-app
npm install
npm run start
```

## 설정

Finder에서 더블클릭으로 실행한 앱은 터미널 환경변수를 물려받지 않는다. 그래서 설정은 이 순서로 읽는다:

1. `~/Library/Application Support/slash-agent-app/config.json`
2. 환경변수(`CONTRACT_AGENT_API_BASE_URL`, `CONTRACT_AGENT_PAIRING_CODE`, `CONTRACT_AGENT_DEVICE_NAME`,
   `CONTRACT_AGENT_HEARTBEAT_INTERVAL_MS`) — 터미널에서 `npm run start`로 실행할 때 유용
3. 기본값(`http://localhost:4000`, 자동 페어링)

앱을 처음 실행하면 위 설정 폴더에 `config.example.json`을 자동으로 만들어 둔다. 메뉴의 **"설정 폴더 열기"**로
Finder에서 바로 찾을 수 있다. `pairingCode`를 안 주면 시험 전용 자동 로그인으로 페어링 코드를 스스로 발급받는다
(`slash-api`가 `/test/login`, `POST /api/v1/pairing-requests`를 제공할 때만 동작 — 실제 운영 백엔드에는 없다).

메뉴바 아이콘 클릭 시 보이는 항목: 상태(연결중/READY/오프라인), 기기 ID, 접속 중인 `slash-api` 주소,
설정 폴더 열기, 종료. Dock 아이콘은 뜨지 않는다(메뉴바 전용, `LSUIElement`).

## 메시지 프로토콜

`slash-agent`는 두 계층의 프로토콜로 `slash-api`와 통신한다.

### 1) 페어링 (HTTP, 최초 1회)

모든 REST 응답은 `{data: {...}, meta: {requestId, serverTime}}` 봉투를 쓰고(에러는
`{error: {code, message}, meta}`), `serverTime`을 포함해 나가는 모든 타임스탬프는 UTC(`Z`)가 아니라
**KST(`+09:00`)**로 표기된다. 아래 요청/응답 필드는 그 `data` 안쪽만 적었다.

```
POST /api/v1/pairing-requests           (사용자가 slash-web에서 발급 — 6자리 코드, 5분 유효)
POST /api/v1/agent/pair                 (Agent → API)
  요청: { pairingCode, publicKey(base64, Ed25519 raw 32byte), device:{name,os,architecture,osVersion,agentVersion}, supportedTaskTypes[] }
  응답: { pairingSessionId, deviceId, challengeId, nonce(base64), expiresAt }

서명 대상 문자열 = `${challengeId}:${nonce}:${deviceId}` (UTF-8), Ed25519 개인키로 서명

POST /api/v1/agent/pair/verify          (Agent → API)
  요청: { pairingSessionId, challengeId, signature(base64) }
  응답: { deviceToken, expiresIn(86400초), issuedAt, wsUrl }
```

`deviceToken`은 24시간 유효하며, 이후 모든 WSS 연결에 `Authorization: Bearer <deviceToken>` 헤더로 쓴다.

**갱신은 기존 토큰을 다시 제시하는 방식이 아니다** — 매번 새 challenge를 다시 밟지 않고도 개인키 보유를
재증명하도록, `refreshNonce`에 대해 다시 서명해야 한다:

```
POST /api/v1/agent/sessions/refresh     (Agent → API)
  요청: { deviceId, refreshNonce(UUID v4), requestedAt(ISO8601), signature(base64) }
  응답: { deviceToken, expiresIn(86400초), issuedAt }
```

서명 대상 문자열 = `${deviceId}:${refreshNonce}:${requestedAt}`. `requestedAt`이 서버 기준 ±120초를
벗어나거나 `refreshNonce`를 재사용하면 거부된다(재전송 공격 방지). 이 저장소의 `contract-agent`는 24시간
안에 재시작하는 짧은 시험 위주라 이 경로를 실제로 타는 코드는 아직 없다 — `slash-api` 쪽 구현·시험만
되어 있다.

### 2) Agent WSS (`/ws/agent`, 접속 후 계속 유지되는 세션)

접속 시 헤더: `Authorization: Bearer <deviceToken>`. 이후 아래 순서로 메시지를 주고받는다.

```
HELLO        Agent → API   최초 1회
CHALLENGE    API → Agent   HELLO에 대한 응답
AUTH         Agent → API   challenge를 Ed25519로 서명해서 회신
READY        Agent → API   AUTH 직후 바로 전송 (API의 별도 승인 응답 없음)
HEARTBEAT    Agent → API   30초 간격 (설정 가능), 90초 무응답 시 API가 기기를 OFFLINE 처리
──────────── 작업이 있을 때만 ────────────
TASK         API → Agent   5초 안에 ACK 없으면 같은 taskId/dispatchId로 1회 재전송
ACK          Agent → API   수락/거부 응답, 5초 안에 보내야 함
PROGRESS     Agent → API   (선택) 진행 상황
RESULT       Agent → API   최종 결과
RESULT_ACK   API → Agent   결과가 저장됐다는 확인
──────────── 오류 시 ────────────
PROTOCOL_ERROR  양방향     스키마 위반, 인증 실패 등
```

공통 필드(모든 메시지): `schemaVersion`("1.0" 고정), `eventId`(UUID v4), `sentAt`(ISO8601, KST `+09:00`). 작업 관련
메시지(TASK/ACK/PROGRESS/RESULT/RESULT_ACK)는 추가로 `taskId`, `dispatchId`, `correlationId`(전부 UUID v4).

#### 메시지별 필드

| 메시지 | 필드 |
|---|---|
| `HELLO` | `deviceId, agentVersion, os(WINDOWS\|MACOS), architecture(X86_64\|ARM64), osVersion, supportedTaskTypes[]` |
| `CHALLENGE` | `challengeId, nonce(base64), expiresAt` |
| `AUTH` | `challengeId, signature(base64)` |
| `READY` | `maxConcurrentTasks, supportedTaskTypes[], searchFolders[{searchFolderId,displayName,indexStatus}], projectWorkspaces[{workspaceId,displayName,workspaceType,availableCodeAdapters[]}]` |
| `HEARTBEAT` | `deviceId, cpuPercent, memoryPercent, runningTaskId(nullable)` |
| `TASK` | `taskType, parameters(object), expiresAt, payloadSha256(64자리 hex)` |
| `ACK` | `accepted(bool), reasonCode(nullable), acknowledgedAt` |
| `PROGRESS` | `stage, percent?(0-100), message?` |
| `RESULT` | `status(SUCCEEDED\|FAILED), result(object\|null), error({code,message,retryable}\|null), startedAt, finishedAt` |
| `RESULT_ACK` | `persisted(bool), taskStatus` |
| `PROTOCOL_ERROR` | `code, message, relatedEventId(nullable), closeConnection(bool)` |

`reasonCode`/`error.code` 값: `DEVICE_BUSY, TASK_TYPE_NOT_SUPPORTED, INVALID_PARAMETERS,
SEARCH_FOLDER_NOT_FOUND, WORKSPACE_NOT_FOUND, CODE_AGENT_NOT_CONFIGURED, TASK_EXPIRED, POLICY_DENIED`.

`payloadSha256`는 존재와 64자리 hex 형식만 검사 대상이다 — 정규화(직렬화) 알고리즘이 아직 백엔드와
합의되지 않아서, 인증 서명이나 무결성 증명으로 취급하지 않는다.

### 3) 지금 이 Agent가 실제로 처리하는 작업

이 Agent는 P0 두 가지만 실행하고 나머지 `taskType`은 `TASK_TYPE_NOT_SUPPORTED`로 거절한다.

- **`SYSTEM_STATUS`**: 이 Mac의 실제 `os.loadavg()`/`os.totalmem()`/`os.freemem()`, `df -k /`(디스크)를
  읽어 `{cpuPercent, memoryPercent, memoryTotalMb, memoryUsedMb, diskPercent, diskTotalMb, diskUsedMb,
  collectedAt}`를 반환한다. parameters 없음.
- **`FILE_SEARCH`**: `parameters.query`로 지정 폴더(`READY`의 `searchFolders`에 등록한 폴더, 패키징된
  앱은 앱 리소스 안의 `fixtures/search-folder`가 기본값) 안의 파일을 이름으로 검색해
  `{items:[{name, relativePath, sizeBytes, modifiedAt}], returnedCount, truncated}`를 반환한다.
  **상대 경로만** 반환하고 로컬 절대 경로는 절대 내보내지 않는다.

### 4) 안정성 관련 동작

- **중복 방지**: 같은 `taskId`+`dispatchId`로 TASK가 다시 오면 재실행하지 않고, 처음 계산했던 ACK/RESULT를
  그대로 다시 보낸다(`RESULT_ACK`를 못 받은 채 재접속한 경우에도 마찬가지 — 재접속 후 READY 직후 자동
  재전송).
- **재연결**: 접속이 끊기면 1초부터 시작해 최대 30초까지 지수 백오프 + jitter로 재시도한다.
- **ACK 타임아웃**: API가 TASK를 보내고 5초 안에 ACK가 없으면 같은 `taskId`/`dispatchId`로 한 번만
  재전송한다.

전체 스키마 정의는 `contracts/src/agentMessages.ts`·`contracts/src/restApi.ts`(zod), 실제 구현은
`contract-agent/src/agent.ts`를 참고. 실제로 오가는 JSON 예시는 [`docs/MESSAGE_GUIDE.md`](docs/MESSAGE_GUIDE.md)에
정리해 뒀다.

## 알려진 한계

- Apple Silicon(arm64)만 빌드/시험했다. Intel Mac, Windows는 아직이다.
- Apple Developer ID 서명·공증(notarization)은 하지 않았다 — 배포하려면 별도로 필요하다.
- `FILE_SEARCH`의 기본 검색 폴더는 시험용 Fixture(`fixtures/search-folder`)다. 실제 사용자 폴더를
  등록/검색하는 UI는 아직 없다(추후 `agent-app`에 폴더 선택 기능을 추가해야 한다).
- 자동 페어링(`obtainPairingCode`)은 `slash-api`의 시험 전용 엔드포인트(`/test/login`)에 의존한다 —
  실제 운영 백엔드에 붙일 때는 `config.json`에 `pairingCode`를 직접 넣어야 한다.

## 관련 저장소

| 저장소 | 역할 |
|---|---|
| [slash-web](https://github.com/LikeLionTeam4/slash-web) | 웹 클라이언트 — React·Vite UI, S3/CloudFront 배포 |
| [slash-api](https://github.com/LikeLionTeam4/slash-api) | 코어 API — 인증, 작업 관리, 실행 위치 결정, DB 연동 |
| [slash-nlu](https://github.com/LikeLionTeam4/slash-nlu) | 자연어 분석 — slash 명령 파싱, 규칙·Kiwi 의도 분류, 인자 추출 |
| [slash-llm](https://github.com/LikeLionTeam4/slash-llm) | LLM 서비스 — Gemma 추론, 요약·대화 생성 |
| **slash-agent** (현재) | 로컬 에이전트 — PC 파일 검색, 상태 조회, 로컬 AI 실행·결과 전달 |
| [slash-infra](https://github.com/LikeLionTeam4/slash-infra) | 인프라 — Terraform(AWS), Helm·ArgoCD 배포 |
| [slash-docs](https://github.com/LikeLionTeam4/slash-docs) | 프로젝트 문서 — 아키텍처, API 계약, ERD, 회의록 |
