// Download only the pinned official sample client; no installer/system writes.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { pipeline } = require('node:stream/promises');
const { Readable } = require('node:stream');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '_experiments/native-browser-feasibility-20261107');
(async () => {
  fs.mkdirSync(lab, { recursive: true });
  const distribution = JSON.parse(fs.readFileSync(path.join(__dirname, 'cef-distribution.json'))).selected;
  const client = distribution.files.find(file => file.type === 'client');
  const destination = path.join(lab, client.name);
  if (!fs.existsSync(destination)) {
    const response = await fetch('https://cef-builds.spotifycdn.com/' + encodeURIComponent(client.name));
    if (!response.ok) throw new Error('Download HTTP ' + response.status);
    await pipeline(Readable.fromWeb(response.body), fs.createWriteStream(destination, { flags: 'wx' }));
  }
  const hash = crypto.createHash('sha1');
  for await (const chunk of fs.createReadStream(destination)) hash.update(chunk);
  const actual = hash.digest('hex');
  if (actual !== client.sha1) throw new Error('Official package checksum mismatch');
  const extract = path.join(lab, 'cef-client');
  fs.mkdirSync(extract, { recursive: true });
  execFileSync('tar', ['-xjf', destination, '-C', extract], { timeout: 120000 });
  fs.writeFileSync(path.join(lab, 'cef-package-verification.json'), JSON.stringify({ cefVersion: distribution.cef_version, archiveBytes: fs.statSync(destination).size, expectedSha1: client.sha1, actualSha1: actual, extract }, null, 2));
  console.log(JSON.stringify({ version: distribution.cef_version, archiveBytes: client.size, sha1Verified: true, extract }));
})().catch(error => { console.error(String(error.stack || error)); process.exitCode = 1; });
