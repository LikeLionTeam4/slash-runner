/**
 * 메시지 프로토콜 문서 §3.5/§11.3: API·WSS·SQS로 나가는 모든 타임스탬프는 UTC("Z")가 아니라
 * 한국 표준시(+09:00) 오프셋으로 표기해야 한다. 값(가리키는 실제 시각)은 그대로 두고 표기만 바꾼다.
 */
export function toIsoKst(date: Date): string {
  const kstShifted = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  return kstShifted.toISOString().replace("Z", "+09:00");
}

export function nowIsoKst(): string {
  return toIsoKst(new Date());
}
