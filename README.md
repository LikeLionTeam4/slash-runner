# Slash | 로컬 에이전트

Slash(/)는 자연어 질문과 `/` 슬래시 명령어를 한 입력창에서 처리하는 AI 에이전트 서비스입니다.
이 저장소는 그중 **사용자 PC에서 동작하는 로컬 에이전트** 파트를 담당합니다.

## 역할

- 사용자 PC 파일 검색
- 상태 조회
- 로컬 AI 에이전트 실행 및 결과 전달

## 시작하기

> 실행 방법·환경변수·디렉터리 구조는 구현이 붙는 대로 이 섹션에 채웁니다.

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
