/**
 * 기기 식별 정보(개인키·deviceId·deviceToken)를 프로세스 재시작 후에도 유지하기 위한 저장소.
 * 실제 보관 방식(OS 보안 저장소 vs 개발용 파일 등)은 이 인터페이스를 구현하는 쪽(agent-app, cli)이 정한다
 * (메시지 프로토콜 문서 §8.1 3단계: "기기 인증 토큰은 운영체제 보안 저장소에 보관한다").
 */
export interface PersistedAgentIdentity {
  deviceId: string;
  deviceToken: string;
  privateKeyPem: string;
  publicKeyBase64: string;
}

export interface AgentIdentityStore {
  load(): Promise<PersistedAgentIdentity | null>;
  save(identity: PersistedAgentIdentity): Promise<void>;
  clear(): Promise<void>;
}
