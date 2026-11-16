// Test harness for the real window/session/notification modules, without spawning
// another backend or touching the user's desktop profile/Quick Chat settings.
import { app } from 'electron';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const profile = path.join(repo, '_experiments/schedule-electron-profile');
fs.mkdirSync(profile, { recursive: true });
app.setPath('userData', profile);
app.setPath('sessionData', profile);
app.setName('Astrion Schedule Integration Test');
app.disableHardwareAcceleration();
await app.whenReady();
const { primeDesktopSession } = await import('../../../desktop-electron/src/session-priming.js');
const { createMainWindow, setMainWindowBackend, focusMainWindow } = await import('../../../desktop-electron/src/window.js');
const { startScheduleNotifications } = await import('../../../desktop-electron/src/schedule-notifications.js');
await primeDesktopSession(8192);
setMainWindowBackend(8192);
const win = createMainWindow(8192, '/schedules');
win.show();
const stopNotifications = startScheduleNotifications(8192, route => focusMainWindow(route));
globalThis.__scheduleHarness = { win, focusMainWindow, profile };
app.once('before-quit', stopNotifications);
