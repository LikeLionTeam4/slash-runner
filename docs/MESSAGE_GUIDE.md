# 메시지 송수신 가이드 — 이 PC 작업 실행기(`slash_pc_runner`, Python 구현)에게 뭘 보내면 뭐가 오는가

이 문서는 다른 팀원이 이 PC 작업 실행기(또는 이 PC 작업 실행기가 구현한 것과 같은 계약을 따르는
실제 PC 작업 실행기)와 연동할 때 "무엇을 보내면 무엇이 돌아오는지"를 실제로 검증된 예시로 보여주는
실전 가이드다. 필드 하나하나의 정의는 [`../README.md`](../README.md)의 "메시지 프로토콜" 절에 이미
다 있으니 여기서는 반복하지 않는다 — 여기는 **실제로 주고받는 JSON 그대로**를 보여주는 데 집중한다.

기준 문서: `260804-1123_SLASH-메시지프로토콜.md`(이하 "프로토콜 문서"). 이 저장소의 `slash-api/mock-api`,
`slash-runner/slash-python-pc-runner`는 이 프로토콜 문서 기준으로 구현·검증되어 있다.

## 0. 큰 그림

```
팀원(프론트/다른 백엔드) → REST POST /api/v1/requests → slash-api → (내부 판단) → PC 작업 실행기 WSS TASK
                                                                              ↓
팀원(프론트)  ←  REST GET /api/v1/tasks/{id}  ←  slash-api  ←  PC 작업 실행기 WSS RESULT
```

대부분의 팀원은 **PC 작업 실행기 WSS를 직접 만지지 않는다** — REST(`POST /api/v1/requests`)로 명령을
보내고 REST(`GET /api/v1/tasks/{taskId}`)로 결과를 받는다. PC 작업 실행기 WSS 왕복(TASK↔ACK↔RESULT)은
`slash-api`와 PC 작업 실행기 사이에서 자동으로 일어난다. 그래서 이 문서는 REST 경로(팀원이 실제로
쓰는 것)와 PC 작업 실행기 WSS 경로(내부에서 실제로 벌어지는 일, 디버깅·검증용)를 둘 다 보여준다.

## 1. 먼저 직접 띄워서 확인하기

```bash
# 터미널 1 — slash-api
npx tsx slash-api/mock-api/src/server.ts

# 터미널 2 — PC 작업 실행기 (slash_pc_runner, Python)
cd slash-runner/slash-python-pc-runner && python -m slash_pc_runner.cli
# 콘솔에 "[slash-pc-runner] 자동 발급된 페어링 코드: NNNNNN" → "READY (deviceId=...)" 가 뜨면 준비된 것
```

아래 모든 예시는 이렇게 띄운 상태에서 그대로 복사해서 실행할 수 있다.

## 2. 팀원이 실제로 쓰는 경로 — REST

### 2.1 로그인 (시험 전용)

```bash
TOKEN=$(curl -s -X POST http://localhost:4000/test/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","displayName":"나"}' | jq -r .token)
```

### 2.2 명령 보내기

```bash
curl -s -X POST http://localhost:4000/api/v1/requests \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"text":"/status"}'
```

**응답** (`202 Accepted`, `/api/v1/**`는 전부 이 `{data,meta}` 봉투를 쓴다 — 프로토콜 문서 §3.3):

```json
{
  "data": {
    "taskId": "2bd119cb-c122-4edb-bb41-017b7ac12986",
    "status": "ANALYZING",
    "statusUrl": "/api/v1/tasks/2bd119cb-c122-4edb-bb41-017b7ac12986"
  },
  "meta": {
    "requestId": "892d4952-0e16-4f52-90cf-3762ae1162ee",
    "serverTime": "2026-08-04T12:10:45.818+09:00"
  }
}
```

`serverTime`처럼 나가는 모든 타임스탬프는 UTC(`Z`)가 아니라 **KST(`+09:00`)**로 표기된다(프로토콜
문서 §3.5). 이건 이번 리팩터링에서 고친 부분이라 특히 눈여겨봐 두면 좋다 — 예전엔 `Z`로 나갔다.

**`SYSTEM_STATUS`/`FILE_SEARCH`처럼 PC가 필요한 명령은 `selectedDeviceId`를 반드시 같이 보내야
한다** — `slash-web` 화면에서는 `READY` 상태 PC를 자동으로 골라서 넣어주지만, 그건 프론트 쪽
편의 로직이지 서버가 알아서 골라주는 게 아니다. curl로 직접 시험할 땐 먼저 PC 목록을 조회해서
`deviceId`를 얻어야 한다:
```bash
curl -s http://localhost:4000/api/v1/devices -H "Authorization: Bearer $TOKEN"
# → {"data":[{"deviceId":"d62591f3-...","status":"READY","...":"..."}],"meta":{...}}

curl -s -X POST http://localhost:4000/api/v1/requests \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d '{"text":"/status","selectedDeviceId":"d62591f3-..."}'
```
`selectedDeviceId`를 안 주면 `task.deviceId`가 `null`로 남아 즉시 `FAILED`(`DEVICE_OFFLINE`)로
끝난다 — 아래 실패 예시 그대로다.

### 2.3 결과 받기 (폴링)

```bash
curl -s http://localhost:4000/api/v1/tasks/2bd119cb-c122-4edb-bb41-017b7ac12986 \
  -H "Authorization: Bearer $TOKEN"
```

`/weather`, `/summary`처럼 로컬 PC가 아예 필요 없는 명령도 있고, `SYSTEM_STATUS`/`FILE_SEARCH`처럼
`selectedDeviceId`로 지정한 PC가 `READY` 상태여야 하는 명령도 있다(§2.4 표 참고). 후자인데 PC를
안 골랐거나 그 PC가 오프라인이면 아래처럼 즉시 실패한다(PC 작업 실행기에는 TASK 자체가 전송되지
않는다):

```json
{
  "data": {
    "taskId": "...",
    "status": "FAILED",
    "errorCode": "DEVICE_OFFLINE",
    "result": null
  },
  "meta": { "...": "..." }
}
```

PC가 `READY`면 최종적으로 이렇게 온다(`SYSTEM_STATUS` 성공):

```json
{
  "data": {
    "taskId": "2bd119cb-c122-4edb-bb41-017b7ac12986",
    "taskType": "SYSTEM_STATUS",
    "processingRoute": "LOCAL_AGENT",
    "status": "SUCCEEDED",
    "result": {
      "cpuPercent": 23.4,
      "memoryPercent": 61.2,
      "memoryTotalMb": 16384,
      "memoryUsedMb": 10025,
      "diskPercent": 42.1,
      "diskTotalMb": 494384,
      "diskUsedMb": 208215,
      "collectedAt": "2026-08-04T12:10:46.001+09:00"
    },
    "errorCode": null
  }
}
```

> `processingRoute: "LOCAL_AGENT"`는 `slash-api`가 정의한 프로토콜 계약의 열거값 그대로다 —
> 이 저장소 자체의 명칭과는 별개이므로 바꾸지 않는다.

### 2.4 각 명령을 보냈을 때 실제로 뭐가 오는지 — 표

| 보낸 `text` | `taskType` | PC 필요? | 성공 시 `result` |
|---|---|---|---|
| `/weather 서울` | `WEATHER_LOOKUP` | 아니오(백엔드 직접 처리) | `{location,temperatureCelsius,condition,precipitationProbability,observedAt,source}` |
| `/status` | `SYSTEM_STATUS` | 예 (`selectedDeviceId` 필수) | 위 예시 참고 |
| `/file 프로젝트` | `FILE_SEARCH` | 예 (`selectedDeviceId` 필수) | 아래 §3.2 참고 |
| `/summary <텍스트>` | `TEXT_SUMMARY` | 아니오(LLM 비동기 처리) | `{summary,inputTokenCount,outputTokenCount,totalTokenCount}` |
| `/weather` (지역 누락) | — | — | `status:"NEEDS_CLARIFICATION"`, `missingRequiredParameters:["location"]`, `result:null` |

## 3. PC 작업 실행기 내부에서 실제로 벌어지는 일 — PC 작업 실행기 WSS

REST로 보낸 명령이 `LOCAL_AGENT` 경로(`SYSTEM_STATUS`, `FILE_SEARCH`)면, `slash-api`가 아래
그대로의 JSON을 PC 작업 실행기에게 WSS로 보내고 받는다. 이 왕복을 팀원이 직접 만들 일은 거의
없지만, "PC 작업 실행기가 왜 이 결과를 냈는지" 디버깅할 때는 이 층을 봐야 한다 —
`GET /api/v1/tasks/{taskId}/events`가 바로 이 왕복을 사람이 읽을 수 있는 타임라인으로 보여준다
(§5 참고).

### 3.1 `SYSTEM_STATUS` — 정상 성공

**받는 것 (slash-api → PC 작업 실행기, `TASK`):**
```json
{
  "schemaVersion": "1.0",
  "type": "TASK",
  "eventId": "111b126d-...",
  "taskId": "2bd119cb-...",
  "dispatchId": "48003562-...",
  "correlationId": "7df8e906-...",
  "taskType": "SYSTEM_STATUS",
  "parameters": {},
  "expiresAt": "2026-08-04T12:11:46.000+09:00",
  "payloadSha256": "…64자리 hex…",
  "sentAt": "2026-08-04T12:10:46.000+09:00"
}
```

**돌려주는 것 (PC 작업 실행기 → slash-api), 순서대로 3통:**
```jsonc
// 1) ACK — 5초 안에 반드시 보낸다
{ "type": "ACK", "accepted": true, "reasonCode": null, "acknowledgedAt": "…", "taskId": "...", "dispatchId": "..." }

// 2) PROGRESS — 선택 사항, 이 PC 작업 실행기는 항상 하나 보낸다
{ "type": "PROGRESS", "stage": "EXECUTING", "percent": 50, "taskId": "...", "dispatchId": "..." }

// 3) RESULT — 최종 결과
{
  "type": "RESULT",
  "status": "SUCCEEDED",
  "result": { "cpuPercent": 23.4, "memoryPercent": 61.2, "...": "..." },
  "error": null,
  "startedAt": "...", "finishedAt": "...",
  "taskId": "...", "dispatchId": "..."
}
```
`slash-api`는 `RESULT`를 저장한 뒤 `RESULT_ACK`로 확인해준다 — PC 작업 실행기는 이걸 받기
전까지 로컬에 결과를 들고 있다가, 못 받고 재접속하면 READY 직후 자동으로 다시 보낸다(중복
전송이지 재실행은 아님).

### 3.2 `FILE_SEARCH` — 정상 성공

**보내는 parameters:** `{"query":"프로젝트","searchFolderId":"sf-fixtures-01","limit":20}`
(`searchFolderId`는 팀원이 몰라도 된다 — `slash-api`가 PC 작업 실행기의 `READY.searchFolders`에서
자동으로 채워준다.)

**RESULT.result** (`fixtures/search-folder/프로젝트_계획.md` 기준 실측):
```json
{
  "items": [
    {
      "name": "프로젝트_계획.md",
      "relativePath": "프로젝트_계획.md",
      "sizeBytes": 842,
      "modifiedAt": "2026-07-30T09:12:00.000+09:00"
    }
  ],
  "returnedCount": 1,
  "truncated": false
}
```
`relativePath`만 나가고 로컬 절대 경로(`/Users/...`)는 **절대 나가지 않는다** — PC 작업 실행기가
검색 폴더 루트 기준 상대 경로로만 응답을 만든다.

### 3.3 실패 케이스들

**지원하지 않는 `taskType`을 보냈을 때** (예: `WEATHER_LOOKUP`을 실수로 PC 작업 실행기 WSS로 보낸
경우 — 정상 흐름에서는 안 일어나지만, 다른 팀원이 직접 WSS를 짤 때 실수하기 쉬운 지점이다):
```json
{ "type": "ACK", "accepted": false, "reasonCode": "TASK_TYPE_NOT_SUPPORTED", "...": "..." }
```
`accepted:false`면 그걸로 끝이다 — `RESULT`는 오지 않는다.

**`FILE_SEARCH`인데 `searchFolderId`가 이 PC 작업 실행기가 아는 값이 아닐 때:**
```json
{ "type": "ACK", "accepted": false, "reasonCode": "SEARCH_FOLDER_NOT_FOUND", "...": "..." }
```

**`query`가 비어있을 때:**
```json
{ "type": "ACK", "accepted": false, "reasonCode": "INVALID_PARAMETERS", "...": "..." }
```

**PC 작업 실행기가 5초 안에 ACK를 아예 안 보낼 때:** `slash-api`가 같은 `taskId`/`dispatchId`로
**1회만** 재전송한다. 그래도 없으면 Task는 `EXPIRED`로 끝난다(`errorCode:"ACK_TIMEOUT"`) — PC
작업 실행기에게 다시 물어볼 필요 없이 REST로 바로 이 상태를 확인할 수 있다.

이 PC 작업 실행기가 실제로 낼 수 있는 `reasonCode`/`error.code` 전체 목록은 프로토콜 문서와 동일한
9종(`DEVICE_BUSY, TASK_TYPE_NOT_SUPPORTED, INVALID_PARAMETERS, SEARCH_FOLDER_NOT_FOUND,
FILE_NOT_FOUND, WORKSPACE_NOT_FOUND, CODE_AGENT_NOT_CONFIGURED, TASK_EXPIRED, POLICY_DENIED`)이며,
이 PC 작업 실행기는 `SYSTEM_STATUS`/`FILE_SEARCH`/`FILE_OPEN`/`AI_AGENT_USAGE`/`CODE_ANALYSIS`/
`TEXT_SUMMARY` 여섯 가지를 지원하므로 실제로는 이 중 일부만 발생한다(자세한 동작은 README.md
"3) 현재 처리하는 작업" 참고).

## 4. 페어링 — PC 작업 실행기가 처음 연결될 때

팀원이 새 PC 작업 실행기(또는 이 Simulator)를 처음 붙일 때만 필요하다. 한 번 `deviceToken`을
받으면 24시간 재사용한다.

```bash
# 1) 프론트에서 등록 코드 발급 (사용자 로그인 필요)
curl -s -X POST http://localhost:4000/api/v1/pairing-requests -H "Authorization: Bearer $TOKEN"
# → {"data":{"pairingRequestId":"...","pairingCode":"483921","expiresAt":"..."},"meta":{...}}

# 2) PC 작업 실행기가 그 코드로 등록 (slash_pc_runner가 자동으로 다 한다 — 아래는 내부적으로 벌어지는 일)
POST /api/v1/agent/pair       { pairingCode, publicKey, device, supportedTaskTypes }
  → { pairingSessionId, deviceId, challengeId, nonce, expiresAt }
POST /api/v1/agent/pair/verify { pairingSessionId, challengeId, signature(Ed25519) }
  → { deviceToken, expiresIn:86400, issuedAt, wsUrl }
```

서명 대상 문자열은 `challengeId:nonce:deviceId`(UTF-8), Ed25519 개인키로 서명한다 — 자세한 내용은
[`../README.md`](../README.md#메시지-프로토콜) 참고.

`deviceToken` 갱신(`POST /api/v1/agent/sessions/refresh`)은 기존 토큰을 다시 제시하는 방식이
**아니다** — 매번 새 `refreshNonce`에 대해 Ed25519로 다시 서명해야 한다(`deviceId:refreshNonce:requestedAt`).
저장된 기기 식별 정보(Keychain)가 있으면 재시작할 때마다 재페어링 대신 이 경로부터 시도하고,
서버가 기기를 못 찾거나(`AUTH_REQUIRED`) 서명 검증에 실패하면 재페어링으로 폴백한다.

## 5. "PC 작업 실행기가 왜 이렇게 답했는지" 확인하는 법

```bash
curl -s http://localhost:4000/api/v1/tasks/{taskId}/events -H "Authorization: Bearer $TOKEN"
```

```json
{
  "data": [
    { "eventId": "...", "fromStatus": null, "toStatus": "ANALYZING", "reasonCode": null, "message": "[slash-api] ANALYZING", "occurredAt": "..." },
    { "eventId": "...", "fromStatus": "ANALYZING", "toStatus": "QUEUED", "reasonCode": null, "message": "[slash-nlu] NLU_RESULT: SYSTEM_STATUS / confidence=1.00", "occurredAt": "..." },
    { "eventId": "...", "fromStatus": "QUEUED", "toStatus": "RUNNING", "reasonCode": null, "message": "[contract-agent] ACK_ACCEPTED: ACK accepted", "occurredAt": "..." },
    { "eventId": "...", "fromStatus": "RUNNING", "toStatus": "SUCCEEDED", "reasonCode": null, "message": "[slash-api] RESULT_PERSISTED: RESULT persisted", "occurredAt": "..." }
  ],
  "meta": { "requestId": "...", "serverTime": "...", "nextCursor": null }
}
```
`message`의 `[대괄호]`는 이 사건이 어느 컴포넌트에서 났는지를 보여준다 — 공개 계약 자체에는 없는
필드(`source`/`eventType`)를 사람이 읽기 쉬운 문자열로 접어 넣은 것이다. `[contract-agent]`는
`slash-api`가 실제로 붙이는 태그 그대로다(다른 저장소 소관이라 이 문서에서 바꾸지 않았다). 실패한
Task라면 `reasonCode`에 그 시점의 `errorCode`가 채워진다.

## 6. 참고: `slash-web` 화면으로 이 전체를 눈으로 보고 싶다면

mock-api(`npx tsx slash-api/mock-api/src/server.ts`), 이 PC 작업 실행기(`python -m
slash_pc_runner.cli` 또는 트레이 앱 `python -m slash_pc_runner.tray_app`), `slash-web`(`npm run
dev`) 셋을 각자 띄운 뒤 `http://localhost:5173`에서 로그인 → PC 등록 코드 발급 → `/status`,
`/file 프로젝트` 등을 직접 입력해보면, 위에서 curl로 본 것과 같은 왕복이 화면의 "통합 흐름"
패널에 그대로 뜬다.
