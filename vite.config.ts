import { fileURLToPath, URL } from 'node:url';
import { execFileSync } from 'node:child_process';
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
// 桌面壳顶部对话标签条（独立 chrome webview 的页面入口，双 webview 架构见
// desktop/src-tauri/src/backend.rs）
const chromeEntry = fileURLToPath(new URL('./static/src/chrome.ts', import.meta.url));

export default defineConfig({
  // 统一静态资源基路径，避免动态 import 走到 /assets/* 导致 404
  base: '/static/dist/',
  plugins: [
    vue(),
    {
      name: 'astrion-entry-styles',
      apply: 'build',
      // Runs after the manifest/CSS are written, including each build --watch update.
      writeBundle() {
        const script = fileURLToPath(
          new URL('./scripts/generate_entry_styles.mjs', import.meta.url)
        );
        const output = execFileSync(process.execPath, [script], { encoding: 'utf8' });
        process.stdout.write(output);
      }
    }
  ],
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
    manifest: true,
    rollupOptions: {
      input: {
        main: entry,
        admin: adminEntry,
        adminPolicy: adminPolicyEntry,
        adminCustomTools: adminCustomToolsEntry,
        adminCustomToolsGuide: adminCustomToolsGuideEntry,
        adminApi: adminApiEntry,
        login: loginEntry,
        register: registerEntry,
        quick: fileURLToPath(new URL('./static/src/quick.ts', import.meta.url)),
        chrome: chromeEntry
      },
      output: {
        // 产物名带内容哈希：URL 本身即版本，避免 CDN/浏览器各自独立缓存与
        // 过期导致「旧 main.js + 新 theme.js」的模块导出不匹配报错
        // （Uncaught SyntaxError: The requested module './theme.js' does not
        // provide an export named 'e'）。
        // 手写 HTML 里的固定名引用由 server/entry_assets.py 在响应时按
        // manifest 替换，源码不感知哈希。
        // 哈希固定 8 位：服务端按该长度识别“固定名”（见 _HASH_RE）。
        entryFileNames: 'assets/[name]-[hash:8].js',
        chunkFileNames: 'assets/[name]-[hash:8].js',
        assetFileNames: 'assets/[name]-[hash:8][extname]'
      }
    }
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./static/src', import.meta.url))
    }
  }
});
