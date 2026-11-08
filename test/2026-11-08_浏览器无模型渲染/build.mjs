import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import vue from '@vitejs/plugin-vue';
const directory = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(directory, '../..');
await build({
  configFile: false,
  root,
  plugins: [vue()],
  resolve: { alias: { '@': path.join(root, 'static/src') } },
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
    __VUE_OPTIONS_API__: 'true',
    __VUE_PROD_DEVTOOLS__: 'false',
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: 'false'
  },
  build: {
    outDir: path.join(root, '_experiments/browser-renderer-regression'),
    emptyOutDir: true,
    lib: { entry: path.join(directory, 'renderer.ts'), formats: ['es'], fileName: () => 'renderer.js' },
    minify: false,
    rollupOptions: { output: { assetFileNames: '[name][extname]' } }
  }
});
