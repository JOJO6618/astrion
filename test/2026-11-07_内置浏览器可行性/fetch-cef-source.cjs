const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const lab = path.join(root, '_experiments/native-browser-feasibility-20261107');
const ref = 'cf671c0'; // Exact source revision encoded in the verified CEF package.
const files = ['include/internal/cef_types_mac.h', 'include/internal/cef_types_win.h', 'tests/cefclient/browser/main_context_impl.cc'];
(async () => {
  const output = path.join(lab, 'cef-source');
  fs.mkdirSync(output, { recursive: true });
  const manifest = await Promise.all(files.map(async file => {
    const response = await fetch(`https://api.github.com/repos/chromiumembedded/cef/contents/${file}?ref=${ref}`, { headers: { Accept: 'application/vnd.github+json' } });
    if (!response.ok) throw new Error(file + ': HTTP ' + response.status);
    const payload = await response.json();
    const content = Buffer.from(payload.content, 'base64').toString('utf8');
    const filename = file.replaceAll('/', '__');
    fs.writeFileSync(path.join(output, filename), content);
    const matches = content.split('\n').flatMap((line, index) => /requires Alloy|runtime_style|parent_view|Using |use_views_|use_alloy_style_/.test(line) ? [{ line: index + 1, text: line }] : []);
    return { ref, file, blobSha: payload.sha, htmlUrl: payload.html_url, savedAs: filename, matches };
  }));
  fs.writeFileSync(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2));
  for (const item of manifest) console.log(JSON.stringify(item));
})().catch(error => { console.error(String(error.stack || error)); process.exitCode = 1; });
