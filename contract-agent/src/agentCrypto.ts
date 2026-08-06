import { generateKeyPairSync, createPrivateKey, sign as cryptoSign, KeyObject } from "node:crypto";

export interface AgentKeyPair {
  publicKeyBase64: string;
  privateKey: KeyObject;
}

/** Ed25519 키쌍 생성. publicKeyBase64는 raw 32byte를 표준 base64로 인코딩한 값(서버 ed25519.ts와 대칭). */
export function generateAgentKeyPair(): AgentKeyPair {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const jwk = publicKey.export({ format: "jwk" }) as { x: string };
  const rawPublicKey = Buffer.from(jwk.x, "base64url");
  return { publicKeyBase64: rawPublicKey.toString("base64"), privateKey };
}

export function signPayload(privateKey: KeyObject, payload: string): string {
  const signature = cryptoSign(null, Buffer.from(payload), privateKey);
  return signature.toString("base64");
}

/** 영속 저장용으로 개인키를 PKCS8 PEM 문자열로 직렬화한다. */
export function exportPrivateKeyPem(privateKey: KeyObject): string {
  return privateKey.export({ format: "pem", type: "pkcs8" }).toString();
}

/** 저장소에서 불러온 PEM + publicKeyBase64로 키쌍을 복원한다(재페어링 없이 재시작하기 위함). */
export function restoreAgentKeyPair(privateKeyPem: string, publicKeyBase64: string): AgentKeyPair {
  const privateKey = createPrivateKey(privateKeyPem);
  return { publicKeyBase64, privateKey };
}
