const { app, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../_experiments/browser-native-integration', `paths-${Date.now()}`);
fs.mkdirSync(root, { recursive: true });
app.setPath('userData', path.join(root, 'electron'));
app.whenReady().then(async () => {
  const paths = [path.join(root, 'outside'), path.join(app.getPath('userData'), 'inside')];
  const rows = [];
  try {
    for (const directory of paths) {
      fs.mkdirSync(directory, { recursive:true, mode:0o700 });
      fs.writeFileSync(path.join(directory, 'main-write-test'), 'ok');
      const ses = session.fromPath(directory, {cache:true});
      await ses.cookies.set({url:'https://fixture.example',name:'persistent',value:'fixture',expirationDate:Math.floor(Date.now()/1000)+86400});
      await ses.cookies.flushStore();
      rows.push({method:'fromPath',path:directory,isPersistent:ses.isPersistent(),cookies:await ses.cookies.get({}),files:fs.readdirSync(directory)});
    }
    const ses = session.fromPartition('persist:reference', {cache:true});
    await ses.cookies.set({url:'https://fixture.example',name:'persistent',value:'fixture',expirationDate:Math.floor(Date.now()/1000)+86400});
    await ses.cookies.flushStore();
    rows.push({method:'fromPartition',path:ses.storagePath,isPersistent:ses.isPersistent(),cookies:await ses.cookies.get({}),files:fs.readdirSync(ses.storagePath)});
    await new Promise(resolve=>setTimeout(resolve,500));
    for (const row of rows) row.filesAfterDelay=fs.readdirSync(row.path);
    fs.writeFileSync(path.join(root, 'results.json'), JSON.stringify(rows,null,2));
    console.log(JSON.stringify({root,rows},null,2));
  } catch(error) {console.error(error);process.exitCode=1;}
  app.exit(process.exitCode||0);
});
