import { execFile } from 'node:child_process';
import path from 'node:path';
import { promisify } from 'node:util';
const execute = promisify(execFile);

export function registryPathValue(text, env = process.env) {
  const match = text.match(/^\s*Path\s+REG_(?:EXPAND_)?SZ\s+(.+)$/im);
  if (!match) return '';
  const values = new Map(Object.entries(env).map(([key, value]) => [key.toLowerCase(), value]));
  return match[1].trim().replace(/%([^%]+)%/g, (raw, name) => values.get(name.toLowerCase()) ?? raw);
}

export async function loadWindowsPath() {
  const reg = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/reg.exe');
  const roots = ['HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment', 'HKCU\\Environment'];
  const results = await Promise.allSettled(roots.map(root => execute(reg, ['query', root, '/v', 'Path'], {
    encoding: 'utf8', timeout: 5000, windowsHide: true, maxBuffer: 256 * 1024
  })));
  const paths = [process.env.Path || process.env.PATH || '', ...results
    .filter(result => result.status === 'fulfilled')
    .map(result => registryPathValue(result.value.stdout))];
  const seen = new Set();
  const entries = paths.flatMap(value => value.split(';')).map(value => value.trim()).filter(Boolean)
    .filter(value => { const key = value.toLowerCase(); if (seen.has(key)) return false; seen.add(key); return true; });
  return { status: results.some(result => result.status === 'fulfilled') ? 'loaded' : 'registry-unavailable', path: entries.join(';') };
}
