const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '_experiments/native-browser-feasibility-20261107');
(async () => {
  const distribution = JSON.parse(fs.readFileSync(path.join(lab, 'cef-distribution.json'))).selected;
  const file = distribution.files.find(item => item.type === 'minimal');
  if (!file) throw new Error('Original official distribution metadata missing minimal SDK');
  const archive = path.join(lab, file.name);
  if (!fs.existsSync(archive)) {
    const response = await fetch('https://cef-builds.spotifycdn.com/' + encodeURIComponent(file.name));
    if (!response.ok) throw new Error('HTTP ' + response.status);
    await pipeline(Readable.fromWeb(response.body), fs.createWriteStream(archive, { flags: 'wx' }));
  }
  const hash = crypto.createHash('sha1');
  for await (const chunk of fs.createReadStream(archive)) hash.update(chunk);
  if (hash.digest('hex') !== file.sha1) throw new Error('SDK checksum mismatch');
  const sdk = path.join(lab, 'sdk');
  fs.mkdirSync(sdk, { recursive: true });
  // Only headers and wrapper build sources, not a second copy of the engine.
  execFileSync('tar', ['-xjf', archive, '-C', sdk, '--include', '*/include/*', '--include', '*/libcef_dll/*', '--include', '*/cmake/*', '--include', '*/CMakeLists.txt'], { timeout: 120000 });
  fs.writeFileSync(path.join(lab, 'sdk-verification.json'), JSON.stringify({ version: distribution.cef_version, archiveBytes: file.size, officialSha1: file.sha1, sha1Verified: true, sdk }, null, 2));
  console.log(JSON.stringify({ sdk, bytes: file.size, sha1Verified: true }));
})().catch(error => { console.error(String(error.stack || error)); process.exitCode = 1; });
