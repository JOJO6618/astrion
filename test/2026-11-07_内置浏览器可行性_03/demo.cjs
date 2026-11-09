// Launch with the project's Electron binary. Closing the native window ends its fixture server.
const { app } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { createFixtureServer } = require('./fixture-server.cjs');
const output = path.resolve(__dirname, '../../_experiments/native-browser-feasibility-20261107/electron-panel', `manual-${Date.now()}`);
fs.mkdirSync(path.join(output, 'profile'), { recursive: true });
app.setPath('userData', path.join(output, 'profile'));
const { server } = createFixtureServer();
server.listen(0, '127.0.0.1', () => {
  process.env.BROWSER_LAB_OUTPUT = output;
  process.env.BROWSER_LAB_ORIGIN = `http://127.0.0.1:${server.address().port}`;
  fs.writeFileSync(path.join(output, 'manual-launch.json'), JSON.stringify({ output,
    origin: process.env.BROWSER_LAB_ORIGIN, pid: process.pid }, null, 2));
  require('./main.cjs');
});
server.on('error', error => { console.error(error); app.exit(1); });
app.on('before-quit', () => { server.closeAllConnections(); server.close(); });
