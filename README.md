# Slash | 로컬 에이전트

Slash(/)는 자연어 질문과 `/` 슬래시 명령어를 한 입력창에서 처리하는 AI 에이전트 서비스입니다.
이 저장소는 그중 **사용자 PC에서 동작하는 로컬 에이전트** 파트를 담당합니다.

## 역할

- 사용자 PC 파일 검색
- 상태 조회
- 로컬 AI 에이전트 실행 및 결과 전달

## 구성

```text
slash-python-agent/   구현 전체 — WebSocket 클라이언트, Ed25519 인증, SQLite FTS5 파일 색인,
                       메뉴바/시스템 트레이 앱(pystray, macOS·Windows 공통)까지 Python 하나로 구성
fixtures/              FILE_SEARCH 데모용 샘플 폴더 (개발 중 실행·패키징된 앱 모두 여기를 기본 검색 폴더로 쓴다)
docs/                   메시지 실전 예시 가이드 (docs/MESSAGE_GUIDE.md)
legacy-macos/           (이 저장소에는 포함되지 않음, .gitignore 처리) — 참고용 구 목업, 로컬에만 존재
```

원래 Electron(TypeScript)으로 구현했다가 Python으로 전환했다 — 유휴 상태 기준 메모리 사용량이
약 10배 차이 나는 것을 실측으로 확인하고 내린 결정이다(`slash-python-agent/FINDINGS.md` 참고).

`slash-python-agent/src/slash_agent/` 안 구성:

```text
protocol.py            메시지 envelope·상수 (schemaVersion, TaskType, ReasonCode 등)
crypto.py               Ed25519 기기 신원(키 생성·서명·PEM 내보내기/복원)
pairing_client.py       HTTP 페어링/토큰 갱신 클라이언트
identity_store.py       기기 식별 정보 영속화 (keyring → macOS Keychain 등 OS 보안 저장소)
processed_task_store.py 중복방지·재전송 이력 영속화
file_index.py           SQLite(FTS5 trigram)+watchdog 다중 폴더 파일 색인
system_status.py        SYSTEM_STATUS 작업 실행 (psutil)
agent.py                핵심 — 연결 루프·재연결·인증·작업 처리 (ContractAgent)
tray_app.py              메뉴바/시스템 트레이 앱 (pystray, macOS·Windows 공통)
folders_window.py       색인 폴더 관리 창 (pywebview, slash-web과 같은 디자인 토큰)
cli.py                   GUI 없는 개발용 CLI 진입점
__main__.py              단일 진입점 — 트레이 기동 / 색인 폴더 창 분기
```

## 실행하기 (개발 모드, 패키징 없이)

```bash
cd slash-python-agent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"

python -m slash_agent.tray_app   # 메뉴바 트레이 앱
# 또는 GUI 없이 콘솔에서만:
python -m slash_agent.cli
```

상태가 `연결 중...`에서 `READY`로 바뀌려면 페어링 대상 백엔드(`slash-api`)가 실행 중이어야 한다.
백엔드가 없는 상태에서도 앱은 종료되지 않으며 트레이 아이콘과 메뉴는 정상적으로 동작한다.

## 빌드해서 실행파일 만들기 (PyInstaller)

### macOS

```bash
cd slash-python-agent
pip install -e ".[build]"
pyinstaller slash_agent.spec
```

- 결과물: `slash-python-agent/dist/Slash Agent.app`
- onedir + macOS `BUNDLE`로 빌드한다 — `Info.plist`에 `LSUIElement`(Dock 아이콘 숨김)가
  설정되어 있어 메뉴바 전용 앱으로 동작한다.
- 아이콘(`AppIcon.icns`)·트레이 아이콘·색인 폴더 관리 창 HTML·기본 시드 폴더
  (`fixtures/search-folder`)를 전부 번들에 포함한다.
- **Apple Silicon(arm64)만 빌드·시험했다.** Intel Mac은 별도 빌드 환경(또는 universal2
  Python)이 있어야 한다 — 코드 자체는 이미 호환되고 빌드만 다시 하면 된다.
- Apple Developer ID 서명·공증(notarization)은 하지 않았다.

### Windows

```powershell
cd slash-python-agent
pip install -e ".[build]"
pyinstaller slash_agent_windows.spec
```

- 결과물: `slash-python-agent/dist/SlashAgent/SlashAgent.exe`
- 트레이(`pystray`)·색인 폴더 관리 창(`pywebview`, WebView2)은 macOS와 같은 코드를 그대로
  쓴다 — 다만 **이 스펙은 macOS에서 작성만 했고 PyInstaller는 크로스 컴파일을 지원하지
  않아, 실제 Windows에서의 빌드·실행 검증은 아직 못했다**(알려진 한계 — Windows 개발
  환경이 없어서). Windows에서 처음 빌드할 때 `hiddenimports`(특히 `pywebview`의
  EdgeChromium/WebView2 백엔드)가 부족해서 실패할 가능성이 있으니 에러 메시지를 보고
  스펙을 조정해야 할 수 있다.

## 설정

설정은 다음 순서로 적용된다:

1. `~/Library/Application Support/slash-agent-py/config.json`(macOS) 또는
   `%APPDATA%\slash-agent-py\config.json`(Windows)
2. 환경변수(`SLASH_AGENT_API_BASE_URL`, `SLASH_AGENT_PAIRING_CODE`, `SLASH_AGENT_DEVICE_NAME`,
   `SLASH_AGENT_HEARTBEAT_INTERVAL_S`) — 터미널에서 `python -m slash_agent.tray_app`으로 실행할 때 유용
3. 기본값(`http://localhost:4000`, 자동 페어링)

앱을 처음 실행하면 위 설정 폴더에 `config.example.json`을 자동으로 생성한다. 메뉴의 **"설정 폴더
열기"**로 Finder(Windows는 탐색기)에서 바로 확인할 수 있다. `pairingCode`를 지정하지 않으면 시험
전용 자동 로그인으로 페어링 코드를 자동 발급받는다(`slash-api`가 `/test/login`,
`POST /api/v1/pairing-requests`를 제공할 때만 동작하며, 실제 운영 백엔드에는 해당 엔드포인트가 없다).

메뉴바/트레이 아이콘 클릭 시 보이는 항목: 상태(연결중/READY/오프라인), 기기 ID, 접속 중인 `slash-api`
주소, **색인 폴더 관리**, 설정 폴더 열기, 종료. macOS에서는 Dock 아이콘이 뜨지 않는다(메뉴바 전용,
Windows는 애초에 이런 개념이 없어 해당 없음).

색인 폴더 관리 창은 별도 프로세스로 뜬다(트레이 아이콘과 GUI 창은 둘 다 같은 프로세스의 메인
스레드 이벤트루프를 동시에 못 써서) — `search-folders.json` 파일을 통해 실행 중인 트레이 앱과
동기화된다(2초 주기로 변경 감지).

> Windows 지원은 트레이 코드(`pystray`)까지는 macOS와 공통이지만, 실제 Windows 환경에서의
> 패키징·실행 검증은 아직 못했다(이 개발 환경이 macOS뿐이라) — 알려진 한계로 남겨둔다.

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
벗어나거나 `refreshNonce`를 재사용하면 요청이 거부된다(재전송 공격 방지). 저장된 기기 식별 정보가
있으면 재시작할 때마다 재페어링 대신 이 경로부터 시도하고, 실패하면 재페어링으로 폴백한다 — 실제로
구현·시험되어 있다.

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

### 3) 현재 이 Agent가 처리하는 작업

이 Agent는 P0로 지정된 두 가지 작업만 실행하며, 그 외의 `taskType`은 `TASK_TYPE_NOT_SUPPORTED`로
거부한다.

- **`SYSTEM_STATUS`**: 이 Mac의 실제 CPU/메모리(`psutil`)·디스크 사용량을 읽어
  `{cpuPercent, memoryPercent, memoryTotalMb, memoryUsedMb, diskPercent, diskTotalMb, diskUsedMb,
  collectedAt}`를 반환한다. parameters 없음.
- **`FILE_SEARCH`**: `parameters.query`로 지정 폴더(`READY`의 `searchFolders`에 등록한 폴더, 기본값은
  `fixtures/search-folder`) 안의 파일을 SQLite FTS5(trigram)로 검색해
  `{items:[{name, relativePath, sizeBytes, modifiedAt}], returnedCount, truncated}`를 반환한다.
  **상대 경로만** 반환하고 로컬 절대 경로는 절대 내보내지 않는다. 폴더는 watchdog로 실시간
  증분 감시된다(재시작 없이 추가/삭제 반영).

### 4) 안정성 관련 동작

- **중복 방지**: 같은 `taskId`+`dispatchId`로 TASK가 다시 오면 재실행하지 않고, 처음 계산했던 ACK/RESULT를
  그대로 다시 보낸다(`RESULT_ACK`를 못 받은 채 재접속한 경우에도 마찬가지 — 재접속 후 READY 직후 자동
  재전송).
- **재연결**: 접속이 끊기면 1초부터 시작해 최대 30초까지 지수 백오프 + jitter로 재시도한다.
- **ACK 타임아웃**: API가 TASK를 보내고 5초 안에 ACK가 없으면 같은 `taskId`/`dispatchId`로 한 번만
  재전송한다.

전체 구현은 `slash-python-agent/src/slash_agent/agent.py`(연결 루프)·`protocol.py`(메시지 상수)를
참고한다. 실제로 오가는 JSON 예시는 [`docs/MESSAGE_GUIDE.md`](docs/MESSAGE_GUIDE.md)에 정리되어 있다.

## 알려진 한계

- Apple Silicon(arm64) macOS에서만 실제로 빌드·시험했다. Intel Mac은 코드는 호환되지만
  별도 빌드 환경이 없어 빌드·시험을 못했고, Windows는 코드(`pystray`+`%APPDATA%` 경로
  분기)까지는 macOS와 공통이지만 `slash_agent_windows.spec`은 Windows 개발 환경이 없어
  실제 빌드·실행 검증을 못한 상태다(둘 다 알려진 한계).
- Apple Developer ID 서명·공증(notarization)은 하지 않았다 — 배포하려면 별도로 필요하다.
- `FILE_SEARCH`의 기본 검색 폴더는 시험용 Fixture(`fixtures/search-folder`)다. 사용자가 색인 폴더
  관리 창으로 폴더를 직접 추가할 수는 있지만, `slash-api`가 여러 폴더 중 검색 대상을 자동으로
  고르는 로직은 아직 첫 번째 등록 폴더만 쓴다(다중 폴더 검색 라우팅은 api·nlu·web 합의 필요).
- 자동 페어링(`_obtain_pairing_code`)은 `slash-api`의 시험 전용 엔드포인트(`/test/login`)에 의존한다 —
  실제 운영 백엔드에 붙일 때는 `config.json`에 `pairingCode`를 직접 넣어야 한다.
- 트레이 앱과 색인 폴더 관리 창을 동시에 띄우면, 둘이 같은 실행 파일을 공유해서 macOS Dock에
  트레이 아이콘이 잠깐 다시 나타나는 잔여 현상이 있다(닫으면 사라짐, 기능에는 영향 없음).

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
