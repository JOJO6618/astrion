import { app, BrowserWindow, nativeImage, screen } from 'electron';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const script = fileURLToPath(import.meta.url);
const root = path.resolve(path.dirname(script), '../..');
const output = path.join(root, 'output/windows-electron/native-capture');
fs.mkdirSync(output, { recursive: true });
const fixtureMode = process.env.ASTRION_CAPTURE_FIXTURE === '1';
const readyPath = path.join(output, 'fixture-ready.json');
const stopPath = path.join(output, 'fixture-stop');
app.setPath('userData', path.join(output, fixtureMode ? 'fixture-profile' : 'userData'));
const checks = [];
const check = (name, ok, detail) => { checks.push({ name, ok: Boolean(ok), detail }); if (!ok) throw new Error(name); };
let fixture;
app.whenReady().then(async () => {
  if (fixtureMode) {
    const window = new BrowserWindow({ width: 640, height: 480, frame: false, show: false,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
    await window.loadURL('data:text/html,<title>Astrion synthetic capture fixture</title><body style="margin:0;background:rgb(30,160,120);width:100vw;height:100vh"></body>');
    window.show();
    window.focus();
    const timer = setInterval(() => { if (fs.existsSync(stopPath)) { clearInterval(timer); window.destroy(); app.quit(); } }, 200);
    setTimeout(() => { clearInterval(timer); window.destroy(); app.quit(); }, 60000).unref();
    setTimeout(() => fs.writeFileSync(readyPath, JSON.stringify({ pid: process.pid, bounds: window.getBounds() })), 800);
    return;
  }
  try {
    const { windowsCaptureList, windowsCaptureWindow, windowsCaptureRegion } = await import(pathToFileURL(path.join(root, 'desktop-electron/src/windows/capture.js')).href);
    const binary = path.join(root, 'desktop-electron/.cache/astrion-windows-helper.exe');
    fs.rmSync(readyPath, { force: true });
    fs.rmSync(stopPath, { force: true });
    fixture = spawn(process.execPath, [script], { env: { ...process.env, ASTRION_CAPTURE_FIXTURE: '1' }, stdio: 'ignore', windowsHide: false });
    let info;
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline && !info) {
      if (fs.existsSync(readyPath)) info = JSON.parse(fs.readFileSync(readyPath, 'utf8'));
      else await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!info) throw new Error(`fixture startup timeout; exit=${fixture.exitCode}`);
    // Production intentionally excludes all windows owned by the shell PID.
    // A second Electron process represents an external application correctly.
    const rows = await windowsCaptureList(binary);
    const row = rows.find(row => row.pid === info.pid && row.app === 'Astrion synthetic capture fixture');
    check('enumeration finds the external synthetic fixture', !!row, { fixture: info, ownRows: rows.filter(row => row.pid === info.pid), total: rows.length });
    const uri = await windowsCaptureWindow(binary, row.id);
    const png = Buffer.from(uri.split(',')[1], 'base64');
    const image = nativeImage.createFromBuffer(png);
    const size = image.getSize();
    check('WGC window capture returns a non-empty PNG', size.width >= 600 && size.height >= 440, size);
    const bitmap = image.toBitmap();
    const center = ((Math.floor(size.height / 2) * size.width) + Math.floor(size.width / 2)) * 4;
    const pixel = [...bitmap.subarray(center, center + 4)];
    check('WGC captures fixture pixels', Math.abs(pixel[0] - 120) < 8 && Math.abs(pixel[1] - 160) < 8 && Math.abs(pixel[2] - 30) < 8, pixel);
    fs.writeFileSync(path.join(output, 'fixture-window.png'), png);
    const bounds = info.bounds;
    const display = screen.getDisplayMatching(bounds);
    const rectangle = { x: bounds.x - display.bounds.x + 80, y: bounds.y - display.bounds.y + 80, width: 160, height: 120 };
    const region = await windowsCaptureRegion(binary, display, rectangle);
    const regionImage = nativeImage.createFromBuffer(Buffer.from(region.split(',')[1], 'base64'));
    const regionSize = regionImage.getSize();
    check('region capture converts DIP selection to physical pixels', Math.abs(regionSize.width - 160 * display.scaleFactor) <= 2 && Math.abs(regionSize.height - 120 * display.scaleFactor) <= 2, regionSize);
  } catch (error) {
    checks.push({ name: 'exception', ok: false, detail: error.stack });
  } finally {
    if (fixture) {
      const exited = new Promise(resolve => fixture.once('exit', resolve));
      fs.writeFileSync(stopPath, 'stop');
      await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 5000))]);
      if (fixture.exitCode === null) fixture.kill();
    }
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ success: checks.every(row => row.ok), checks }, null, 2));
    app.exit(checks.every(row => row.ok) ? 0 : 1);
  }
});
