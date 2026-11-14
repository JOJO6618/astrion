const path = require('node:path');
const base = require('../../desktop-electron/electron-builder.win.cjs');

// A complete, unpublished next-version package. Keep the production appId,
// executable, main entry, runtime and NSIS hooks unchanged. Only the version
// and artifact output differ; no test bootstrap replaces production startup.
module.exports = {
  ...base,
  directories: {
    ...base.directories,
    output: path.resolve(__dirname, '../../output/windows-update-real-20261114/build-next'),
  },
  extraMetadata: { ...base.extraMetadata, version: '0.10.1' },
  publish: { provider: 'generic', url: 'http://127.0.0.1:19976/', channel: 'latest' },
};
