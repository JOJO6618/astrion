import { spawn } from 'node:child_process';
import { windowsHelper } from '../../windows/native-helper.js';

const MAX_BYTES = 32 * 1024 * 1024;
const ERRORS = new Set(['invalid_source', 'invalid_import_selection', 'invalid_import_file',
  'import_file_too_large', 'import_file_too_many_records', 'chrome_read_failed']);

/** Only the trusted credentials service calls this; no renderer-provided paths. */
export async function readWindowsChrome(request, { signal, resolveHelper = windowsHelper, env = process.env } = {}) {
  if (!['sources', 'preview-history', 'preview-file'].includes(request.action)) throw new Error('chrome_read_failed');
  if (signal?.aborted) throw new Error('import_cancelled');
  const input = Buffer.from(JSON.stringify(request));
  if (input.length > MAX_BYTES) { input.fill(0); throw new Error('import_file_too_large'); }
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
      child = spawn(resolveHelper(), ['chrome-import'], {
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
