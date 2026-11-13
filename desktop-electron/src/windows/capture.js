import { screen } from 'electron';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const execute = promisify(execFile);

async function image(binary, args) {
  const { stdout } = await execute(binary, args.map(String), {
    encoding: 'buffer', maxBuffer: 50 * 1024 * 1024, timeout: 15000, windowsHide: true
  });
  if (stdout.length < 8 || stdout.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') {
    throw new Error('Windows native capture returned an invalid PNG');
  }
  return `data:image/png;base64,${stdout.toString('base64')}`;
}

export async function windowsCaptureRegion(binary, display, rect) {
  // The helper is PMv2-aware and captures physical pixels. Screen bounds from
  // Electron are DIP; convert the monitor origin and local selection separately.
  const origin = screen.dipToScreenPoint({ x: display.bounds.x, y: display.bounds.y });
  const scale = display.scaleFactor || 1;
  return image(binary, ['region', origin.x + 1, origin.y + 1,
    rect.x * scale, rect.y * scale, rect.width * scale, rect.height * scale]);
}

export async function windowsCaptureWindow(binary, id) {
  if (typeof id !== 'string' || !/^\d+:\d+$/.test(id)) throw new Error('Invalid Windows capture window');
  return image(binary, ['window', id]);
}

export async function windowsCaptureList(binary) {
  const { stdout } = await execute(binary, ['windows', String(process.pid)], {
    encoding: 'utf8', maxBuffer: 4 * 1024 * 1024, timeout: 4000, windowsHide: true
  });
  const rows = JSON.parse(stdout);
  if (!Array.isArray(rows)) throw new Error('Invalid Windows native window list');
  return rows.filter(row => typeof row.id === 'string' && /^\d+:\d+$/.test(row.id))
    .map(row => ({ ...row, ...screen.screenToDipRect(null, {
      x: row.x, y: row.y, width: row.width, height: row.height
    }) }));
}
