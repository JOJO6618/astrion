import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { app } from 'electron';

const MAX_BYTES = 32 * 1024 * 1024;
const ERRORS = new Set(['invalid_source', 'invalid_import_selection', 'invalid_import_file',
  'import_file_too_large', 'import_file_too_many_records', 'chrome_read_failed']);

export function windowsChromeRuntime() {
  if (app.isPackaged) {
    return {
      python: path.join(process.resourcesPath, 'runtime/python/astrion-backend.exe'),
      worker: path.join(process.resourcesPath, 'chrome-import/windows-worker.py')
    };
  }
  return {
    python: fileURLToPath(new URL('../../../../desktop/src-tauri/runtime/python/python.exe', import.meta.url)),
    worker: fileURLToPath(new URL('../../../../desktop/src-tauri/src/browser/chrome_worker.py', import.meta.url))
  };
}

/** Only the trusted credentials service calls this; no renderer-provided paths. */
export function readWindowsChrome(request, { signal, runtime = windowsChromeRuntime(), env = process.env } = {}) {
  if (!['sources', 'preview', 'preview-file'].includes(request.action)) throw new Error('chrome_read_failed');
  const input = Buffer.from(JSON.stringify(request));
  if (input.length > MAX_BYTES) { input.fill(0); throw new Error('import_file_too_large'); }
  if (![runtime.python, runtime.worker].every(file => fs.existsSync(file))) {
    input.fill(0); throw new Error('chrome_read_failed');
  }
  return new Promise((resolve, reject) => {
    let child, timer, complete = false, size = 0;
    const chunks = [];
    const finish = (error, value) => {
      if (complete) return;
      complete = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      input.fill(0);
      for (const chunk of chunks) chunk.fill(0);
      if (error) reject(error); else resolve(value);
    };
    const stop = code => { child?.kill(); finish(new Error(code)); };
    const abort = () => stop('import_cancelled');
    try {
      child = spawn(runtime.python, ['-I', '-B', runtime.worker], {
        env, windowsHide: true, stdio: ['pipe', 'pipe', 'ignore']
      });
      child.on('error', () => finish(new Error('chrome_read_failed')));
      child.stdin.on('error', () => {});
      child.stdout.on('data', chunk => {
        if (complete) { chunk.fill(0); return; }
        size += chunk.length;
        if (size > MAX_BYTES) { chunk.fill(0); stop('chrome_read_failed'); return; }
        chunks.push(chunk);
      });
      child.on('close', code => {
        if (complete) return;
        const output = Buffer.concat(chunks);
        try {
          if (code !== 0) throw new Error('chrome_read_failed');
          const response = JSON.parse(output.toString('utf8'));
          if (!response.ok) throw new Error(ERRORS.has(response.error) ? response.error : 'chrome_read_failed');
          finish(null, response.value);
        } catch (error) { finish(new Error(ERRORS.has(error.message) ? error.message : 'chrome_read_failed')); }
        finally { output.fill(0); }
      });
      timer = setTimeout(() => stop('chrome_read_failed'), 60000);
      signal?.addEventListener('abort', abort, { once: true });
      if (signal?.aborted) { abort(); return; }
      child.stdin.end(input, () => input.fill(0));
    } catch { finish(new Error('chrome_read_failed')); }
  });
}
