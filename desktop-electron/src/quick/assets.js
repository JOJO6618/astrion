import { app, protocol } from 'electron';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { quickDocument } from './entry-styles.js';

// A stable origin preserves drafts/session selection across app restarts. This
// serves only renderer assets; all authenticated requests remain behind IPC.
export async function startQuickAssets() {
  const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
  const root = app.isPackaged ? path.join(process.resourcesPath, 'runtime/backend/static') : path.join(repo, 'static');
  const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf' };
  protocol.handle('astrion-quick', async (request) => {
    try {
      const url = new URL(request.url);
      if (url.hostname !== 'app' || request.method !== 'GET') return new Response(null, { status: 403 });
      const relative = url.pathname === '/quick' ? 'quick.html' : url.pathname.replace(/^\/static\//, '');
      const target = await fs.realpath(path.resolve(root, `.${path.sep}${decodeURIComponent(relative)}`));
      const actualRoot = await fs.realpath(root);
      if (!target.startsWith(actualRoot + path.sep) || !mime[path.extname(target)]) return new Response(null, { status: 403 });
      let bytes = await fs.readFile(target);
      if (relative === 'quick.html') {
        const manifest = JSON.parse(await fs.readFile(path.join(root, 'dist/.vite/manifest.json'), 'utf8'));
        bytes = Buffer.from(quickDocument(bytes.toString('utf8'), manifest));
      }
      return new Response(bytes, { headers: { 'Content-Type': mime[path.extname(target)], 'Cache-Control': 'no-store' } });
    } catch { return new Response(null, { status: 404 }); }
  });
  return { url: 'astrion-quick://app/quick', close: () => protocol.unhandle('astrion-quick') };
}
