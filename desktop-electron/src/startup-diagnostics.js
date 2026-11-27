import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

const logName = 'astrion-desktop-startup.log';

// Preserve the original fatal error even when GUI launches have no stderr.
// A private fallback directory avoids overwriting an existing file in /tmp.
export function writeStartupFailure(error, preferredDirectory, fallbackDirectory = os.tmpdir()) {
  const text = `[astrion-desktop] 启动失败\n${error?.stack || error}\n`;
  if (preferredDirectory) {
    const temporary = path.join(preferredDirectory, `.${logName}.${randomUUID()}`);
    try {
      fs.mkdirSync(preferredDirectory, { recursive: true });
      fs.writeFileSync(temporary, text, { flag: 'wx', mode: 0o600 });
      const target = path.join(preferredDirectory, logName);
      fs.renameSync(temporary, target);
      return target;
    } catch {
      try { fs.unlinkSync(temporary); } catch { /* No temporary output to remove. */ }
    }
  }
  try {
    const directory = fs.mkdtempSync(path.join(fallbackDirectory, 'astrion-startup-'));
    const target = path.join(directory, logName);
    fs.writeFileSync(target, text, { flag: 'wx', mode: 0o600 });
    return target;
  } catch {
    return undefined;
  }
}

export function resolveStartupBackend(resolve, reportFailure) {
  try {
    return resolve();
  } catch (error) {
    reportFailure(error);
    throw error;
  }
}
