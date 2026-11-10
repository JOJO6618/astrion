import { build } from 'vite';
import vue from '@vitejs/plugin-vue';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
await build({
  configFile: false,
  root: here,
  plugins: [vue()],
  resolve: { alias: { '@': path.join(root, 'static/src') } },
  build: {
    outDir: path.join(root, '_experiments/browser-green-fixture'),
    emptyOutDir: true,
    minify: false,
    sourcemap: false
  },
  css: { preprocessorOptions: { scss: { silenceDeprecations: ['legacy-js-api'] } } },
  logLevel: 'warn'
});
console.log('Built current BrowserPanel and theme/overlay code.');
