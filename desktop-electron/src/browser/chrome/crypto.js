import { createDecipheriv, createHash, pbkdf2Sync, timingSafeEqual } from 'node:crypto';
const decoder = new TextDecoder('utf-8', { fatal: true });

export function deriveChromeKey(secret) {
  if (!Buffer.isBuffer(secret) || !secret.length) throw new Error('Chrome 钥匙串密钥不可用。');
  return pbkdf2Sync(secret, 'saltysalt', 1003, 16, 'sha1');
}
export function decryptChromeValue(hex, key, cookie) {
  const encrypted = Buffer.from(hex || '', 'hex');
  if (encrypted.subarray(0, 3).toString('ascii') !== 'v10') throw new Error('unsupported_encryption');
  if (!key || encrypted.length <= 3 || (encrypted.length - 3) % 16) throw new Error('invalid_encrypted_value');
  const decipher = createDecipheriv('aes-128-cbc', key, Buffer.alloc(16, 0x20));
  let plaintext;
  try {
    plaintext = Buffer.concat([decipher.update(encrypted.subarray(3)), decipher.final()]);
    let value = plaintext;
    if (cookie?.version >= 24) {
      const hash = createHash('sha256').update(cookie.domain).digest();
      if (plaintext.length < 32 || !timingSafeEqual(hash, plaintext.subarray(0, 32))) throw new Error('cookie_domain_mismatch');
      value = plaintext.subarray(32);
    }
    return decoder.decode(value);
  } finally { plaintext?.fill(0); }
}
export function chromeTime(value) {
  try {
    const milliseconds = BigInt(String(value || '0')) / 1000n - 11644473600000n;
    const number = Number(milliseconds);
    return number > 0 && Number.isSafeInteger(number) ? number : 0;
  } catch { return 0; }
}
