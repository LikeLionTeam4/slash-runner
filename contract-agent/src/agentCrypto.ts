import { generateKeyPairSync, sign as cryptoSign, KeyObject } from "node:crypto";

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
