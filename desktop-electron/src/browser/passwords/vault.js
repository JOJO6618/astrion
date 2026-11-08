import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { safeStorage } from 'electron';

export function passwordOrigin(value) {
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('密码网站地址必须是 HTTP 或 HTTPS。');
  return url.origin;
}
function cleanRecord(input) {
  const origin = passwordOrigin(input.origin);
  if (typeof input.username !== 'string' || input.username.length > 4096 || typeof input.password !== 'string' || !input.password || input.password.length > 16384) throw new Error('账号或密码格式不正确。');
  return { origin, username: input.username, password: input.password };
}
const metadata = ({ id, origin, username, updatedAt, usedAt }) => ({ id, origin, username, updatedAt, usedAt });
function regular(file) {
  try {
    const stat = fs.lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) throw new Error('密码库文件不是常规独立文件。');
    return true;
  } catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}
export class PasswordVault {
  constructor(directory, encryption = safeStorage) {
    this.file = path.join(directory, 'passwords.enc');
    this.encryption = encryption;
    this.data = null;
  }
  load() {
    if (this.data) return this.data;
    if (!regular(this.file)) return this.data = { version: 1, records: [], settings: { save: true, autofill: true } };
    if (!this.encryption.isEncryptionAvailable()) throw new Error('系统钥匙串暂不可用，密码库未解锁。');
    try {
      const fd = fs.openSync(this.file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
      let encrypted;
      try {
        const stat = fs.fstatSync(fd);
        if (!stat.isFile() || stat.nlink !== 1 || stat.size > 64 * 1024 * 1024) throw new Error('invalid');
        encrypted = fs.readFileSync(fd);
      } finally { fs.closeSync(fd); }
      let value;
      try { value = JSON.parse(this.encryption.decryptString(encrypted)); } finally { encrypted.fill(0); }
      if (value.version !== 1 || !Array.isArray(value.records) || value.records.length > 50000) throw new Error('invalid');
      const records = value.records.map(row => ({ ...cleanRecord(row), id: String(row.id), updatedAt: Number(row.updatedAt) || 0, usedAt: Number(row.usedAt) || 0 }));
      this.data = { version: 1, records, settings: { save: value.settings?.save !== false, autofill: value.settings?.autofill !== false } };
      return this.data;
    } catch { throw new Error('无法解锁密码库，请检查本机钥匙串授权。'); }
  }
  persist(next) {
    if (!this.encryption.isEncryptionAvailable()) throw new Error('系统钥匙串不可用，未保存密码。');
    const encrypted = this.encryption.encryptString(JSON.stringify(next));
    const temporary = `${this.file}.${randomUUID()}.tmp`;
    try {
      regular(this.file);
      fs.writeFileSync(temporary, encrypted, { flag: 'wx', mode: 0o600 });
      fs.renameSync(temporary, this.file);
      fs.chmodSync(this.file, 0o600);
      this.data = next;
    } finally {
      encrypted.fill(0);
      try { fs.unlinkSync(temporary); } catch { /* Atomic rename already removed it. */ }
    }
  }
  list() { const data = this.load(); return { records: data.records.map(metadata), settings: { ...data.settings } }; }
  matches(origin) { return this.load().records.filter(row => row.origin === passwordOrigin(origin)); }
  get(id) {
    const row = this.load().records.find(row => row.id === id);
    if (!row) throw new Error('这条密码记录已不存在。');
    return row;
  }
  find(origin, username) { return this.matches(origin).find(row => row.username === username); }
  save(input, id) {
    const value = cleanRecord(input), data = this.load(), current = id ? this.get(id) : null;
    if (data.records.some(row => row.origin === value.origin && row.username === value.username && row.id !== current?.id)) throw new Error('该网站已有同名账号，请编辑已有记录。');
    const row = { ...value, id: current?.id || randomUUID(), updatedAt: Math.max(Date.now(), (current?.updatedAt || 0) + 1), usedAt: current?.usedAt || 0 };
    const records = current ? data.records.map(item => item.id === current.id ? row : item) : [...data.records, row];
    if (records.length > 50000) throw new Error('密码库记录数量已达到上限。');
    this.persist({ ...data, records });
    return metadata(row);
  }
  remove(id) { this.get(id); const data = this.load(); this.persist({ ...data, records: data.records.filter(row => row.id !== id) }); }
  configure(settings) {
    if (typeof settings?.save !== 'boolean' || typeof settings?.autofill !== 'boolean') throw new Error('密码设置格式不正确。');
    this.persist({ ...this.load(), settings: { save: settings.save, autofill: settings.autofill } });
  }
  importRows(rows) {
    const data = this.load(), records = data.records.map(row => ({ ...row }));
    for (const input of rows) {
      const value = cleanRecord(input), index = records.findIndex(row => row.origin === value.origin && row.username === value.username), current = records[index];
      const row = { ...value, id: current?.id || randomUUID(), updatedAt: Math.max(Date.now(), (current?.updatedAt || 0) + 1), usedAt: current?.usedAt || 0 };
      if (index < 0) records.push(row); else records[index] = row;
    }
    if (records.length > 50000) throw new Error('密码库记录数量已达到上限。');
    this.persist({ ...data, records });
  }
}
