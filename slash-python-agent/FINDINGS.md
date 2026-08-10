# PyInstaller / Python 에이전트 실현 가능성 검증 결과

## 배경

Electron 기반 agent-app이 실제로 쓰는 핵심 기능이 Python + PyInstaller 조합으로도
동작하는지, 그리고 실제 성능 차이가 얼마나 나는지 확인한다.

`legacy-macos/docs/decisions/0001-mock-scope-and-environment-constraints.md`의 D12
항목에 이미 "PyInstaller가 macOS에서 실제로 빌드·실행됐고 사소한 버그 1개만 있었다"는
기록이 있었으나, Keychain·SQLite FTS5·트레이 아이콘·실제 WSS 프로토콜 조합은 검증된
적이 없어서 이번에 확인했다.

## 1단계 — 패키징 실현 가능성 (`feasibility_check.py`)

macOS Keychain 접근(`keyring`), SQLite FTS5 파일 색인, 메뉴바 트레이 아이콘(`rumps`) 셋
다 PyInstaller `--windowed` 기본 옵션만으로, 별도 `hiddenimports` 없이 얼린 `.app`
번들에서 통과했다(비간힌 venv 상태와 동일하게 재현됨).

## 2단계 — 실제 프로토콜 왕복 (`contract_agent.py`)

contract-agent(TypeScript)의 핵심 흐름(HTTP 페어링 → WSS HELLO/CHALLENGE/AUTH/READY →
HEARTBEAT → TASK/ACK/PROGRESS/RESULT)을 Python으로 이식해 `slash-api-test`의 실제
mock-api에 붙여서 검증했다(재검증·재연결·영속화 등은 이번 범위 밖 — 성능 비교 목적).

```
[slash-python-agent] 페어링 시작 (pairingCode=340686)
[slash-python-agent] 페어링 완료 deviceId=e7fba5bb-...
[slash-python-agent] READY 전송 완료
[slash-python-agent] TASK 처리 완료 taskId=ceecfaed-...
```

실제 `POST /api/v1/requests`로 `SYSTEM_STATUS` 작업을 만들어 끝까지 `SUCCEEDED`로
완료되는 것까지 확인했다(mock-api의 task 조회 응답에 실제 CPU/메모리/디스크 값 포함).

## 메모리 실측 비교

| 상태 | Electron(agent-app) | Python(venv, 비간힌 상태) |
|---|---:|---:|
| 유휴(트레이만, 통신 없음) | 약 220 MB(멀티프로세스: 메인+Helper 2개) | 약 52~72 MB(PyInstaller로 얼린 상태, 단일 프로세스) |
| **실제 페어링+WSS 연결+Heartbeat 통신 중** | **약 219.5 MB** | **약 22 MB** |

WBS 목표치(유휴 Memory 200MiB 이하) 기준으로 Electron은 유휴 상태부터 이미 초과, 통신
중에도 거의 같은 수준을 유지한다. Python은 통신 중에도 목표치의 1/9 수준이다.

## 시작 시간·CPU (참고, 1단계 트레이 전용 측정 기준)

| 지표 | Electron | Python+PyInstaller |
|---|---:|---:|
| 트레이 준비까지 | 약 1,040 ms | 약 333 ms |
| 유휴 CPU | 0~0.2% | 0~0.1%(사실상 동일) |

## 결론

- **PyInstaller 자체는 문제가 없었다.** `legacy-macos` D12의 기존 발견과 이번 검증이
  같은 결론이다 — "PyInstaller가 실패해서 Electron으로 갔다"는 서술은 근거가 약하다.
- **메모리는 실제 통신 상태에서도 Python 쪽이 약 10배 가볍다** — 트레이만 띄운
  비교가 아니라 실제 페어링·WSS·TASK 처리까지 포함한 공정한 비교에서도 격차가
  유지·확대됐다.
- Electron을 선택할 다른 근거들도 재검토했다:
  - **"Claude Code CLI와 Node 런타임 궁합"은 근거가 약했다** — Anthropic이 공식
    Python SDK(`claude-agent-sdk`, 2026-08-04 릴리스)를 배포 중이라 Python도
    동일하게 1급 지원을 받는다.
  - **계약 스키마 공유**는 사람이 아니라 AI가 양쪽을 유지하는 이번 개발 방식에서는
    상대적으로 약한 이유다.
  - **크로스플랫폼 GUI 개발 일관성**만 여전히 유효한 이유로 남는다(Mac/Windows
    트레이·설정 창 구현이 Python에선 OS별로 갈라질 위험).
- **Windows는 여전히 미검증**이다 — Windows 실기기가 없어 이번에도 확인하지 못했다.
- 이 결과는 "Electron 유지 vs Python 전환"을 팀이 다시 논의할 만한 실측 근거이며,
  이미 검증 끝난 기능(w1-02/05/06/07)을 전부 다시 만드는 전체 이식은 이번 스코프에
  포함하지 않았다.

## 검증 방법 (재현 절차)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[build]"

# 1단계: 패키징 가능성
pyinstaller --windowed --name SlashPyAgent --noconfirm feasibility_check.py
open dist/SlashPyAgent.app   # ~/slash-python-agent-results.log 에서 결과 확인

# 2단계: 실제 프로토콜 (slash-api-test의 mock-api 필요)
python contract_agent.py --api-base-url http://localhost:4000 --pairing-code <코드>
```
