import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { QuickGateway } from './gateway.js';
const registry = path.join(os.homedir(), '.astrion/quick-entry-gateway.json');

export async function advertiseGateway(port, dataRoot) {
  await fs.mkdir(path.dirname(registry), { recursive: true });
  const temporary = `${registry}.${process.pid}.tmp`;
  await fs.writeFile(temporary, JSON.stringify({ port, dataRoot, pid: process.pid }), { mode: 0o600 });
  await fs.rename(temporary, registry);
}

export async function discoverGateway() {
  try {
    const candidate = JSON.parse(await fs.readFile(registry, 'utf8'));
    if (!Number.isInteger(candidate.port) || candidate.port < 1 || candidate.port > 65535 || !path.isAbsolute(candidate.dataRoot)) return {};
    const gateway = new QuickGateway(candidate);
    // Probe only: a stale record never starts a server or terminates another process.
    await gateway.request('/api/host/workspaces');
    return { port: candidate.port, dataRoot: candidate.dataRoot };
  } catch { return {}; }
}
