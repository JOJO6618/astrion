import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const entry = fileURLToPath(new URL('./static/src/main.ts', import.meta.url));
const adminEntry = fileURLToPath(new URL('./static/src/admin/main.ts', import.meta.url));
const adminPolicyEntry = fileURLToPath(
  new URL('./static/src/admin/policyMain.ts', import.meta.url)
);
const adminCustomToolsEntry = fileURLToPath(
  new URL('./static/src/admin/customToolsMain.ts', import.meta.url)
);
const adminCustomToolsGuideEntry = fileURLToPath(
  new URL('./static/src/admin/customToolsGuideMain.ts', import.meta.url)
);
const adminApiEntry = fileURLToPath(new URL('./static/src/admin/apiMain.ts', import.meta.url));
const loginEntry = fileURLToPath(new URL('./static/src/auth/loginMain.ts', import.meta.url));
const registerEntry = fileURLToPath(new URL('./static/src/auth/registerMain.ts', import.meta.url));

export default defineConfig({
  // 统一静态资源基路径，避免动态 import 走到 /assets/* 导致 404
  base: '/static/dist/',
  plugins: [vue()],
  css: {
    preprocessorOptions: {
      scss: {
        silenceDeprecations: ['legacy-js-api']
      },
      sass: {
        silenceDeprecations: ['legacy-js-api']
      }
    }
  },
  build: {
    outDir: 'static/dist',
    emptyOutDir: false,
    rollupOptions: {
      input: {
        main: entry,
        admin: adminEntry,
        adminPolicy: adminPolicyEntry,
        adminCustomTools: adminCustomToolsEntry,
        adminCustomToolsGuide: adminCustomToolsGuideEntry,
        adminApi: adminApiEntry,
        login: loginEntry,
        register: registerEntry
      },
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name][extname]'
      }
    }
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./static/src', import.meta.url))
    }
  }
});
