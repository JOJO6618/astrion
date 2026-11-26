import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { QuickGateway } from './gateway.js';
const registry = process.env.ASTRION_QUICK_DISCOVERY_FILE || (process.env.ASTRION_ELECTRON_USER_DATA
  ? path.join(process.env.ASTRION_ELECTRON_USER_DATA, 'quick-entry-gateway.json')
  : path.join(os.homedir(), '.astrion/quick-entry-gateway.json'));

export async function advertiseGateway(port, dataRoot, dataDir) {
  await fs.mkdir(path.dirname(registry), { recursive: true });
  const temporary = `${registry}.${process.pid}.tmp`;
  await fs.writeFile(temporary, JSON.stringify({ port, dataRoot, dataDir, pid: process.pid }), { mode: 0o600 });
  await fs.rename(temporary, registry);
}

export async function discoverGateway() {
  try {
    const candidate = JSON.parse(await fs.readFile(registry, 'utf8'));
    if (!Number.isInteger(candidate.port) || candidate.port < 1 || candidate.port > 65535
      || typeof candidate.dataRoot !== 'string' || !path.isAbsolute(candidate.dataRoot)
      || (candidate.dataDir !== undefined && (typeof candidate.dataDir !== 'string' || !path.isAbsolute(candidate.dataDir)))) return {};
    const gateway = new QuickGateway(candidate);
    // Probe only: a stale record never starts a server or terminates another process.
    await gateway.request('/api/host/workspaces');
    return { port: candidate.port, dataRoot: candidate.dataRoot, dataDir: candidate.dataDir };
  } catch { return {}; }
}
