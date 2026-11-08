const { app } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..');
const directory = process.env.BROWSER_PERSISTENCE_TEST_ROOT;
if (!directory || !directory.startsWith(path.join(root, '_experiments/browser-native-integration') + path.sep)) throw new Error('test_directory_required');
fs.mkdirSync(directory, { recursive: true });
app.setPath('userData', path.join(directory, 'electron'));
app.whenReady().then(async () => {
  try {
    const { BrowserProfile } = await import(pathToFileURL(path.join(root, 'desktop-electron/src/browser/profile.js')));
    const profile = new BrowserProfile(path.join(directory, 'metadata'), 'restart-fixture');
    if (process.argv.includes('--write')) {
      await profile.session.cookies.set({url:'https://fixture.example',name:'restart-cookie',value:'saved-across-process',expirationDate:Date.now()/1000+86400});
      profile.record('https://fixture.example/', 'Restart fixture');
      await profile.close();
      fs.writeFileSync(path.join(directory, 'write.json'), JSON.stringify({pid:process.pid,storage:profile.session.storagePath}));
    } else {
      const written = JSON.parse(fs.readFileSync(path.join(directory, 'write.json'), 'utf8'));
      const cookies = await profile.session.cookies.get({url:'https://fixture.example'});
      assert.notEqual(process.pid, written.pid);
      assert.ok(cookies.some(cookie=>cookie.name==='restart-cookie' && cookie.value==='saved-across-process'));
      assert.equal(profile.data.history[0].title, 'Restart fixture');
      await profile.close();
      const result={passed:true,writerPid:written.pid,readerPid:process.pid,checks:['persistent cookie survives actual Electron process restart','history metadata survives actual process restart']};
      fs.writeFileSync(path.join(directory, 'results.json'), JSON.stringify(result,null,2));
      console.log(JSON.stringify(result));
    }
  } catch(error) { console.error(error.stack); process.exitCode=1; }
  app.exit(process.exitCode||0);
});
