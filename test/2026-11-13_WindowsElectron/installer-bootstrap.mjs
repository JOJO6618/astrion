// Included only in the separate installer-test product. A restart from Explorer
// may discard inherited environment variables, so isolation is baked into its
// package metadata and applied before importing the real production entrypoint.
import fs from 'node:fs';
import path from 'node:path';
import { app } from 'electron';
if (!app.commandLine.hasSwitch('remote-debugging-port')) {
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
  app.commandLine.appendSwitch('remote-debugging-port', '19876');
}
const metadata = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
const root = metadata.astrionMigrationTestRoot;
if (!root || !path.isAbsolute(root)) throw new Error('Missing absolute installer-test data root');
const defaults = {
  ASTRION_ELECTRON_USER_DATA: path.join(root, 'profile'),
  ASTRION_DESKTOP_DATA_ROOT: path.join(root, 'data'),
  ASTRION_DESKTOP_SETTINGS_FILE: path.join(root, 'rundata-settings.json'),
  ASTRION_QUICK_SETTINGS_FILE: path.join(root, 'quick-settings.json'),
  ASTRION_QUICK_DISCOVERY_FILE: path.join(root, 'gateway.json'),
  ASTRION_DESKTOP_INITIAL_WORKSPACE: path.join(root, 'workspace')
};
for (const [key, value] of Object.entries(defaults)) process.env[key] ||= value;
await import('../src/main.js');
