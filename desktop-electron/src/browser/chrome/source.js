import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as delay } from 'node:timers/promises';
import { app } from 'electron';
const execute = promisify(execFile);
const PROFILE = /^(?:Default|Profile \d+)$/;
export const chromeRoot = () => path.join(app.getPath('home'), 'Library/Application Support/Google/Chrome');

function regular(file) {
  try { const stat = fs.lstatSync(file); return stat.isFile() && !stat.isSymbolicLink(); } catch { return false; }
}
export function chromeProfiles(root = chromeRoot()) {
  if (!fs.existsSync(root)) return [];
  const stat = fs.lstatSync(root);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Chrome 资料目录不是有效的本机目录。');
  let names = {};
  try { names = JSON.parse(fs.readFileSync(path.join(root, 'Local State'), 'utf8')).profile?.info_cache || {}; } catch { /* Directory IDs remain usable without display names. */ }
  return fs.readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isDirectory() && PROFILE.test(entry.name))
    .map(entry => ({ id: entry.name, name: String(names[entry.name]?.name || entry.name).slice(0, 200) }));
}
export function profileDirectory(id, root = chromeRoot()) {
  if (!chromeProfiles(root).some(row => row.id === id)) throw new Error('请选择有效的 Chrome 用户资料。');
  return path.join(root, id);
}
export function existingDatabase(directory, names) {
  return names.map(name => path.join(directory, name)).filter(regular);
}
export function tableColumns(database, table) {
  if (!['logins', 'cookies', 'urls', 'meta'].includes(table)) throw new Error('不支持的 Chrome 数据表。');
  return new Set(database.prepare(`PRAGMA table_info(${table})`).all().map(row => row.name));
}
export async function readDatabase(file, read, signal) {
  if (!regular(file)) throw new Error('missing_database');
  // Keep every schema/data query on one read transaction, including committed WAL data.
  // Short waits yield between attempts so Chrome's exclusive History lock cannot freeze UI.
  for (let attempt = 0; attempt < 4; attempt++) {
    signal?.throwIfAborted();
    let database;
    try {
      database = new DatabaseSync(file, { readOnly: true });
      database.exec('PRAGMA query_only=ON; PRAGMA busy_timeout=100; BEGIN;');
      database.prepare('SELECT count(*) FROM sqlite_schema').get();
      return read(database);
    } catch (error) {
      if (!['SQLITE_BUSY', 'SQLITE_LOCKED'].includes(error.errstr) && !/locked|busy/i.test(error.message)) throw new Error('database_unavailable');
      if (attempt === 3) throw new Error('database_busy');
    } finally { database?.close(); }
    await delay(150, undefined, { signal });
  }
}
export async function chromeEncryptionKey(signal) {
  try {
    const { stdout } = await execute('/usr/bin/security', ['find-generic-password', '-s', 'Chrome Safe Storage', '-a', 'Chrome', '-w'],
      { encoding: 'buffer', maxBuffer: 16384, timeout: 300000, signal });
    // Remove only the newline appended by security, never trim actual key material.
    const end = stdout.length - (stdout.at(-1) === 10 ? 1 : 0);
    const secret = Buffer.from(stdout.subarray(0, end));
    stdout.fill(0);
    if (!secret.length) throw new Error('empty_key');
    return secret;
  } catch (error) {
    if (signal?.aborted) throw new Error('import_cancelled');
    throw new Error(error.killed ? 'keychain_timeout' : 'keychain_denied');
  }
}
