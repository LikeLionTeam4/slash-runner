# Slash | PC 작업 실행기

Slash(/)는 자연어 질문과 `/` 슬래시 명령어를 하나의 입력창에서 처리하는 AI 에이전트 서비스입니다.
이 저장소는 그중 **사용자 PC에서 동작하는 PC 작업 실행기(slash-pc-runner)** 부분을 담당합니다.

## 역할

- 사용자 PC 파일 검색
- 상태 조회
- 로컬 AI 실행 및 결과 전달

## 구성

```text
slash-python-pc-runner/ 구현 전체 — WebSocket 클라이언트, Ed25519 인증, SQLite FTS5 파일 색인,
                       메뉴바/시스템 트레이 앱(pystray, macOS·Windows 공통)까지 Python으로 구성됩니다.
fixtures/              FILE_SEARCH 데모용 샘플 폴더입니다(개발·패키징된 앱 모두 기본 검색 폴더로 사용).
docs/                   메시지 실전 예시 가이드입니다(docs/MESSAGE_GUIDE.md).
legacy-macos/           이 저장소에는 포함되지 않습니다(.gitignore 처리) — 참고용 구 목업이며 로컬에만 존재합니다.
```

원래 Electron(TypeScript)으로 구현했으나 Python으로 전환했습니다. 유휴 상태 기준 메모리 사용량에서
약 10배 차이가 확인되어 내린 결정입니다(자세한 내용은 `slash-python-pc-runner/FINDINGS.md` 참고).

`slash-python-pc-runner/src/slash_pc_runner/` 구성:

```text
protocol.py             메시지 envelope·상수(schemaVersion, TaskType, ReasonCode 등)
crypto.py               Ed25519 기기 신원(키 생성·서명·PEM 내보내기/복원)
pairing_client.py       HTTP 페어링/토큰 갱신 클라이언트
identity_store.py       기기 식별 정보 영속화(keyring → macOS Keychain 등 OS 보안 저장소)
processed_task_store.py 중복 방지·재전송 이력 영속화
file_index.py           SQLite(FTS5 trigram)+watchdog 다중 폴더 파일 색인
file_actions.py         FILE_OPEN 작업 실행(파일이 위치한 폴더를 파일 탐색기로 표시)
system_status.py        SYSTEM_STATUS 작업 실행(psutil)
usage_adapters.py       AI_AGENT_USAGE 작업 실행(로컬 Claude Code·Codex 사용량 조회)
code_adapters.py        CODE_ANALYSIS 작업 실행(로컬 Claude Code·Codex CLI 호출, 읽기 전용)
agent.py                핵심 로직 — 연결 루프·재연결·인증·작업 처리(ContractPcRunner)
_build_info.py          빌드 커밋 SHA·날짜(agentVersion·트레이 메뉴에 노출)
update_check.py         GitHub Releases 기준 최신 버전 확인(앱 시작 시 1회)
single_instance.py      중복 실행 방지(named mutex/lock)
resources.py            리소스 경로 해석(개발 모드 vs PyInstaller 번들)
tray_app.py              메뉴바/시스템 트레이 앱(pystray, macOS·Windows 공통)
folders_window.py       색인 폴더 관리 창(pywebview, slash-web과 동일한 디자인 토큰 사용)
cli.py                   GUI 없는 개발용 CLI 진입점
__main__.py              단일 진입점 — 트레이 기동 / 색인 폴더 창 분기
```

## 실행 방법(개발 모드, 패키징 없이)

```bash
cd slash-python-pc-runner
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"

python -m slash_pc_runner.tray_app   # 메뉴바 트레이 앱
# 또는 GUI 없이 콘솔에서만 실행:
python -m slash_pc_runner.cli
```

상태가 `연결 중...`에서 `READY`로 전환되려면 페어링 대상 백엔드(`slash-api`)가 실행 중이어야 합니다.
백엔드가 없는 상태에서도 앱은 종료되지 않으며, 트레이 아이콘과 메뉴는 정상적으로 동작합니다.

## 빌드 및 실행 파일 생성(PyInstaller)

### macOS

```bash
cd slash-python-pc-runner
pip install -e ".[build]"
pyinstaller slash_pc_runner.spec
```

- 결과물: `slash-python-pc-runner/dist/Slash.app`
- onedir + macOS `BUNDLE`로 빌드합니다. `Info.plist`에 `LSUIElement`(Dock 아이콘 숨김)가
  설정되어 있어 메뉴바 전용 앱으로 동작합니다.
- 아이콘(`AppIcon.icns`), 트레이 아이콘, 색인 폴더 관리 창 HTML, 기본 시드 폴더
  (`fixtures/search-folder`)를 모두 번들에 포함합니다.
- **Apple Silicon(arm64) 환경에서만 빌드 및 검증을 완료했습니다.** Intel Mac은 별도 빌드 환경
  (또는 universal2 Python)이 필요하며, 코드 자체는 이미 호환되므로 재빌드만 하면 됩니다.
- Apple Developer ID 서명 및 공증(notarization)은 아직 진행하지 않았습니다.

### Windows

```powershell
cd slash-python-pc-runner
pip install -e ".[build]"
pyinstaller slash_pc_runner_windows.spec
```

- 결과물: `slash-python-pc-runner/dist/Slash/Slash.exe`
  (Windows 쪽 앱 표시 이름도 macOS(`Slash.app`)와 통일하여 `SlashAgent` → `Slash`로 정리했으며,
  실기기에서 재검증을 완료했습니다.)
- 트레이(`pystray`)와 색인 폴더 관리 창(`pywebview`, WebView2)은 macOS와 동일한 코드를 사용합니다.
- **Windows 11(빌드 10.0.26200)에서 빌드 및 실행을 검증했습니다.** `pip install -e ".[build,test]"`,
  `pytest`(75건 통과), 개발 모드 실행(Mac의 mock-api에 LAN으로 연결해 페어링·READY 확인),
  `pyinstaller slash_pc_runner_windows.spec` 빌드까지 모두 확인했습니다. 현재 `hiddenimports`
  목록만으로 빌드가 성공하며 추가 조정은 필요하지 않습니다. 트레이 아이콘 툴팁과 작업표시줄
  컨텍스트 메뉴 모두 "Slash"로 표시되며, 단일 인스턴스 락(`SlashTray` named mutex)도 이름 변경
  이후 재검증했습니다(`Slash.exe`를 연속 실행해도 트레이 아이콘은 1개만 유지됩니다).
- 검증 과정에서 다음 두 가지 결함을 확인하고 수정했습니다(코드가 macOS와 공통이므로 동일하게
  적용됩니다).
  1. `folders_window.py`가 `webview.start()`에 아이콘을 지정하지 않아, 색인 폴더 관리 창의
     작업표시줄/Dock 아이콘이 앱 로고 대신 `sys.executable`(개발 모드에서는 python.exe) 아이콘으로
     표시되는 문제. `winforms.py`·`cocoa.py` 백엔드 모두 동일한 폴백 로직을 사용하므로 macOS에도
     같은 문제가 있었으나, Dock이 숨겨져 있어 드러나지 않았습니다. `AppIcon.ico`/`AppIcon.icns`를
     명시적으로 전달하도록 수정했습니다.
  2. `tray_app.py`의 `quit_app()`이 색인 폴더 관리 창(별도 프로세스)의 핸들을 보유하지 않아,
     해당 창을 띄운 상태로 트레이를 종료해도 창이 닫히지 않던 문제. 핸들을 저장해 종료 시 함께
     정리하도록 수정했습니다.
- 참고 사항(코드 결함은 아닙니다). 개발 모드로 실행한 `python -m slash_pc_runner.tray_app`을
  종료하지 않은 상태로 패키징된 `Slash.exe`를 실행하면, 두 프로세스가 WebView2 사용자 데이터
  폴더를 공유하여 색인 폴더 관리 창을 열 때 한 번 종료되는 현상을 확인했습니다. 이전 인스턴스의
  `msedgewebview2.exe` 잔여 프로세스를 정리한 뒤 재실행하면 재현되지 않습니다. 개발 모드와
  패키징된 실행 파일을 동시에 실행하지 않으면 문제가 없습니다.
- 리네이밍 이후 환경변수 이름도 `SLASH_AGENT_API_BASE_URL` 등에서 `SLASH_PC_RUNNER_API_BASE_URL`
  등으로 변경되었습니다. 개발 모드 실행 시 이전 이름을 사용하면 기본값(`http://localhost:4000`)이
  적용되어 연결이 거부된 것처럼 보일 수 있으니 주의가 필요합니다.

## 설정

설정은 다음 순서로 적용됩니다.

1. `~/Library/Application Support/slash-pc-runner-py/config.json`(macOS) 또는
   `%APPDATA%\slash-pc-runner-py\config.json`(Windows)
2. 환경변수(`SLASH_PC_RUNNER_API_BASE_URL`, `SLASH_PC_RUNNER_PAIRING_CODE`,
   `SLASH_PC_RUNNER_DEVICE_NAME`, `SLASH_PC_RUNNER_HEARTBEAT_INTERVAL_S`) — 터미널에서
   `python -m slash_pc_runner.tray_app`으로 실행할 때 유용합니다.
3. 기본값(`http://localhost:4000`, 자동 페어링)

앱을 처음 실행하면 위 설정 폴더에 `config.example.json`이 자동으로 생성됩니다. 메뉴의
**"설정 폴더 열기"**로 Finder(Windows는 탐색기)에서 바로 확인할 수 있습니다. `pairingCode`를
지정하지 않으면 시험 전용 자동 로그인으로 페어링 코드를 자동 발급받습니다(`slash-api`가
`/test/login`, `POST /api/v1/pairing-requests`를 제공하는 경우에만 동작하며, 실제 운영
백엔드에는 해당 엔드포인트가 없습니다).

메뉴바/트레이 아이콘 클릭 시 표시되는 항목은 상태(연결중/READY/오프라인), 기기 ID, 접속 중인
`slash-api` 주소, 버전·커밋·빌드일자(각각 별도 줄), **색인 폴더 관리**, 설정 폴더 열기,
종료입니다. macOS에서는 Dock 아이콘이 표시되지 않습니다(메뉴바 전용 앱이며, Windows에는
해당 개념이 없어 무관합니다).

앱 시작 시 GitHub Releases를 1회 조회해 더 최신 버전이 있으면 OS 알림을 띄우고, 메뉴에도
"새 버전 있음: vX.Y.Z" 줄이 추가로 나타납니다(클릭하면 릴리스 페이지가 열립니다). 조회에
실패해도(오프라인 등) 조용히 넘어가며 앱 동작에는 영향이 없습니다.

색인 폴더 관리 창은 별도 프로세스로 실행됩니다(트레이 아이콘과 GUI 창이 같은 프로세스의 메인
스레드 이벤트 루프를 동시에 사용할 수 없기 때문입니다). `search-folders.json` 파일을 통해 실행
중인 트레이 앱과 동기화되며, 변경 사항은 2초 주기로 감지됩니다.

> Windows 11에서 개발 모드 실행, PyInstaller 패키징, `Slash.exe` 실행까지 모두 검증을
> 완료했습니다(위 "빌드 및 실행 파일 생성(PyInstaller) > Windows" 항목 참고).

## 메시지 프로토콜

PC 작업 실행기(slash-pc-runner)는 두 계층의 프로토콜로 `slash-api`와 통신합니다.

### 1) 페어링(HTTP, 최초 1회)

모든 REST 응답은 `{data: {...}, meta: {requestId, serverTime}}` 형식을 사용하며(에러는
`{error: {code, message}, meta}`), `serverTime`을 포함한 모든 타임스탬프는 UTC(`Z`)가 아닌
**KST(`+09:00`)**로 표기됩니다. 아래 요청/응답 필드는 `data` 내부만 표기했습니다.

```
POST /api/v1/pairing-requests           (사용자가 slash-web에서 발급 — 6자리 코드, 5분 유효)
POST /api/v1/agent/pair                 (PC 작업 실행기 → API)
  요청: { pairingCode, publicKey(base64, Ed25519 raw 32byte), device:{name,os,architecture,osVersion,agentVersion}, supportedTaskTypes[] }
  응답: { pairingSessionId, deviceId, challengeId, nonce(base64), expiresAt }

서명 대상 문자열 = `${challengeId}:${nonce}:${deviceId}` (UTF-8), Ed25519 개인키로 서명

POST /api/v1/agent/pair/verify          (PC 작업 실행기 → API)
  요청: { pairingSessionId, challengeId, signature(base64) }
  응답: { deviceToken, expiresIn(86400초), issuedAt, wsUrl }
```

`deviceToken`은 24시간 동안 유효하며, 이후 모든 WSS 연결에 `Authorization: Bearer <deviceToken>`
헤더로 사용됩니다.

**갱신은 기존 토큰을 다시 제시하는 방식이 아닙니다.** 매번 새 challenge를 거치지 않고도 개인키
보유를 재증명할 수 있도록, `refreshNonce`에 대해 다시 서명하는 방식을 사용합니다.

```
POST /api/v1/agent/sessions/refresh     (PC 작업 실행기 → API)
  요청: { deviceId, refreshNonce(UUID v4), requestedAt(ISO8601), signature(base64) }
  응답: { deviceToken, expiresIn(86400초), issuedAt }
```

서명 대상 문자열 = `${deviceId}:${refreshNonce}:${requestedAt}`. `requestedAt`이 서버 기준
±120초를 벗어나거나 `refreshNonce`를 재사용하면 요청이 거부됩니다(재전송 공격 방지). 저장된
기기 식별 정보가 있는 경우 재시작할 때마다 재페어링 대신 이 경로를 먼저 시도하며, 실패하면
재페어링으로 전환합니다. 해당 동작은 실제로 구현 및 검증을 완료했습니다.

> `POST /api/v1/agent/**`, `/ws/agent` 등 URL 경로의 `agent`는 `slash-api`가 정의한 프로토콜
> 계약(다른 저장소와 공유)을 그대로 따릅니다. 이 저장소의 명칭과는 별개이므로 변경하지 않습니다.

### 2) PC 작업 실행기 WSS(`/ws/agent`, 접속 후 유지되는 세션)

접속 시 헤더: `Authorization: Bearer <deviceToken>`. 이후 아래 순서로 메시지를 주고받습니다.

```
HELLO        PC 작업 실행기 → API   최초 1회
CHALLENGE    API → PC 작업 실행기   HELLO에 대한 응답
AUTH         PC 작업 실행기 → API   challenge를 Ed25519로 서명해 회신
READY        PC 작업 실행기 → API   AUTH 직후 즉시 전송(API의 별도 승인 응답 없음)
HEARTBEAT    PC 작업 실행기 → API   30초 간격(설정 가능), 90초 무응답 시 API가 기기를 OFFLINE 처리
──────────── 작업이 있을 때만 ────────────
TASK         API → PC 작업 실행기   5초 내 ACK가 없으면 동일한 taskId/dispatchId로 1회 재전송
ACK          PC 작업 실행기 → API   수락/거부 응답, 5초 이내 전송 필요
PROGRESS     PC 작업 실행기 → API   진행 상황(선택)
RESULT       PC 작업 실행기 → API   최종 결과
RESULT_ACK   API → PC 작업 실행기   결과 저장 확인
──────────── 오류 시 ────────────
PROTOCOL_ERROR  양방향     스키마 위반, 인증 실패 등
```

공통 필드(모든 메시지): `schemaVersion`("1.0" 고정), `eventId`(UUID v4), `sentAt`(ISO8601,
KST `+09:00`). 작업 관련 메시지(TASK/ACK/PROGRESS/RESULT/RESULT_ACK)는 `taskId`, `dispatchId`,
`correlationId`(전부 UUID v4)를 추가로 포함합니다.

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

> 위 필드 이름(`agentVersion` 등)도 `slash-api`가 정의한 프로토콜 계약의 일부이므로 이 저장소가
> 단독으로 변경할 수 없습니다. 실제로 서버에 전송하는 **값**은 `slash-pc-runner-py/0.4.0`처럼
> 패키지 버전을 그대로 담습니다.

`reasonCode`/`error.code` 값: `DEVICE_BUSY, TASK_TYPE_NOT_SUPPORTED, INVALID_PARAMETERS,
SEARCH_FOLDER_NOT_FOUND, FILE_NOT_FOUND, WORKSPACE_NOT_FOUND, CODE_AGENT_NOT_CONFIGURED,
TASK_EXPIRED, POLICY_DENIED`

`payloadSha256`은 존재 여부와 64자리 hex 형식만 검사 대상입니다. 정규화(직렬화) 알고리즘이
아직 백엔드와 합의되지 않아, 인증 서명이나 무결성 증명으로는 사용하지 않습니다.

### 3) 현재 처리하는 작업

PC 작업 실행기는 다섯 가지 `taskType`을 처리하며, 그 외는 `TASK_TYPE_NOT_SUPPORTED`로
거부합니다.

- **`SYSTEM_STATUS`**: 실행 중인 PC의 CPU/메모리(`psutil`)·디스크 사용량을 조회하여
  `{cpuPercent, memoryPercent, memoryTotalMb, memoryUsedMb, diskPercent, diskTotalMb,
  diskUsedMb, collectedAt}`를 반환합니다. parameters는 없습니다.
- **`FILE_SEARCH`**: `parameters.{query, searchFolderId}`로 지정 폴더(`READY`의
  `searchFolders`에 등록된 폴더, 기본값은 `fixtures/search-folder`) 내 파일을 SQLite
  FTS5(trigram)로 검색하여 `{searchFolderId, query, items:[{fileRef, name, relativePath,
  extension, sizeBytes, modifiedAt}], returnedCount, truncated}`를 반환합니다. **상대
  경로만** 반환하며 로컬 절대 경로는 노출하지 않습니다. `fileRef`는 로컬에만 존재하는
  불투명 식별자로, 이후 `FILE_OPEN`이 이 값으로 같은 파일을 다시 찾습니다. 폴더는
  watchdog로 실시간 증분 감시되어 재시작 없이 추가/삭제가 반영됩니다.
- **`FILE_OPEN`**: `parameters.fileRef`로 지정한 파일을 파일 탐색기(macOS는 Finder,
  Windows는 탐색기)에서 **선택된 상태로 위치만 표시**하고 `{revealedAt}`을 반환합니다.
  기본 연결 프로그램으로 파일을 실행하지 않습니다(임의 파일 실행 방지). 등록 해제된
  폴더나 삭제된 파일을 가리키는 `fileRef`는 실행 시점에 재검증해 `FILE_NOT_FOUND`로
  거부합니다.
- **`AI_AGENT_USAGE`**: `parameters.provider`(`CLAUDE_CODE` 또는 `CODEX`)의 로컬 세션 로그
  (`~/.claude/projects/`, `~/.codex/sessions/`)를 **읽기만** 해서 토큰 사용량을 집계하여
  `{provider, totalSessions, totalInputTokens, totalOutputTokens, totalCachedTokens,
  totalReasoningTokens, totalTokens, oldestSessionAt, newestSessionAt, collectedAt}`를
  반환합니다. 해당 CLI를 쓴 적이 없어 로그 디렉터리 자체가 없으면
  `CODE_AGENT_NOT_CONFIGURED`로 거부합니다.
- **`CODE_ANALYSIS`**: `parameters.{workspaceId, query, codeAdapter?}`로 지정한 프로젝트
  폴더에서 로컬에 설치된 `claude`/`codex` CLI를 실제로 **실행**해 분석 결과를 받습니다.
  쓰기·셸 실행 도구는 CLI 플래그로 구조적으로 차단해 읽기 전용입니다.
  `{codeAdapter, summary, turns, durationMs, collectedAt}`를 반환하며, 결과가 서버 저장
  상한(64KB)을 넘으면 잘라서 보냅니다. 등록되지 않은 워크스페이스는 `WORKSPACE_NOT_FOUND`,
  CLI가 설치되어 있지 않으면 `CODE_AGENT_NOT_CONFIGURED`로 거부합니다.

### 4) 안정성 관련 동작

- **중복 방지**: 동일한 `taskId`+`dispatchId`로 TASK가 재전송되면 재실행하지 않고, 처음
  계산한 ACK/RESULT를 그대로 다시 전송합니다(`RESULT_ACK`를 받지 못한 채 재접속한 경우에도
  READY 직후 자동으로 재전송됩니다).
- **재연결**: 접속이 끊기면 1초부터 시작해 최대 30초까지 지수 백오프와 jitter를 적용해
  재시도합니다.
- **ACK 타임아웃**: API가 TASK를 전송한 뒤 5초 내 ACK가 없으면 동일한 `taskId`/`dispatchId`로
  1회 재전송합니다.

전체 구현은 `slash-python-pc-runner/src/slash_pc_runner/agent.py`(연결 루프)와 `protocol.py`
(메시지 상수)를 참고하십시오. 실제 메시지 예시는 [`docs/MESSAGE_GUIDE.md`](docs/MESSAGE_GUIDE.md)에
정리되어 있습니다.

## 알려진 한계

- Apple Silicon(arm64) macOS와 Windows 11에서 빌드 및 실행을 검증했습니다. Intel Mac은 코드
  자체는 호환되지만 별도 빌드 환경이 없어 검증하지 못했습니다.
- Apple Developer ID 서명 및 공증(notarization)은 진행하지 않았습니다. 배포 시 별도로
  필요합니다.
- `FILE_SEARCH`의 기본 검색 폴더는 시험용 Fixture(`fixtures/search-folder`)입니다. 사용자가
  색인 폴더 관리 창에서 폴더를 직접 추가할 수 있으나, 여러 폴더 중 검색 대상을 자동으로
  선택하는 로직은 아직 첫 번째 등록 폴더만 사용합니다(다중 폴더 검색 라우팅은 api·nlu·web
  간 합의가 필요합니다).
- 자동 페어링(`_obtain_pairing_code`)은 `slash-api`의 시험 전용 엔드포인트(`/test/login`)에
  의존합니다. 실제 운영 백엔드에 연결할 때는 `config.json`에 `pairingCode`를 직접 입력해야
  합니다.
- 트레이 앱과 색인 폴더 관리 창을 동시에 실행하면, 두 프로세스가 같은 실행 파일을 공유하여
  macOS Dock에 트레이 아이콘이 잠시 다시 나타나는 현상이 있습니다(창을 닫으면 사라지며,
  기능에는 영향이 없습니다).

## 관련 저장소

| 저장소 | 역할 |
|---|---|
| [slash-web](https://github.com/LikeLionTeam4/slash-web) | 웹 클라이언트 — React·Vite UI, S3/CloudFront 배포 |
| [slash-api](https://github.com/LikeLionTeam4/slash-api) | 코어 API — 인증, 작업 관리, 실행 위치 결정, DB 연동 |
| [slash-nlu](https://github.com/LikeLionTeam4/slash-nlu) | 자연어 분석 — slash 명령 파싱, 규칙·Kiwi 의도 분류, 인자 추출 |
| [slash-llm](https://github.com/LikeLionTeam4/slash-llm) | LLM 서비스 — Gemma 추론, 요약·대화 생성 |
| **[slash-runner](https://github.com/LikeLionTeam4/slash-runner)**(현재) | PC 작업 실행기(slash-pc-runner) — PC 파일 검색, 상태 조회, 로컬 AI 실행·결과 전달 |
| [slash-infra](https://github.com/LikeLionTeam4/slash-infra) | 인프라 — Terraform(AWS), Helm·ArgoCD 배포 |
| [slash-docs](https://github.com/LikeLionTeam4/slash-docs) | 프로젝트 문서 — 아키텍처, API 계약, ERD, 회의록 |
