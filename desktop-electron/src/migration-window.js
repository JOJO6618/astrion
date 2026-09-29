import { BrowserWindow } from 'electron';

export const migrationPageHtml = String.raw`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<title>Astrion</title>
<style>
  :root { color-scheme: light dark; font: 13px/1.45 -apple-system, BlinkMacSystemFont, sans-serif; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 18px 22px 16px; color: CanvasText; background: Canvas; user-select: none; }
  .title { margin: 0 0 14px; font-size: 14px; font-weight: 600; }
  .bar { height: 6px; overflow: hidden; border-radius: 3px; background: color-mix(in srgb, CanvasText 12%, Canvas); }
  .fill { width: 0; height: 100%; border-radius: inherit; background: Highlight; transition: width .18s ease; }
  .fill.busy { width: 35%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { from { margin-left: -35%; } to { margin-left: 100%; } }
  .meta { display: flex; justify-content: space-between; gap: 12px; margin-top: 11px; color: GrayText; font-size: 12px; }
  .detail { white-space: nowrap; }
  .error { color: Mark; }
  .action { display: none; min-height: 30px; margin: 12px 0 0 auto; padding: 0 12px; border: 1px solid color-mix(in srgb, CanvasText 18%, Canvas); border-radius: 6px; color: CanvasText; background: Canvas; font: inherit; cursor: pointer; }
  .action:hover { background: color-mix(in srgb, CanvasText 6%, Canvas); }
</style>
</head>
<body>
  <h1 class="title" id="title"></h1>
  <div class="bar"><div class="fill" id="fill"></div></div>
  <div class="meta"><span id="status"></span><span class="detail" id="detail"></span></div>
  <button class="action" id="action" type="button"></button>
<script>
  const zh = (navigator.language || '').toLowerCase().startsWith('zh');
  const text = zh ? {
    moving: '正在迁移运行数据…', done: '数据迁移完成', failed: '数据迁移未完成',
    preparing: '准备中', scanning: '正在统计文件…', copying: '正在复制数据…', verifying: '正在校验…',
    items: ' 项', continue: '继续使用原目录', close: '关闭', cancel: '取消迁移并退出', quit: '退出应用', failure: '迁移失败：', unknown: '未知错误',
    errors: { verification_failed: '复制后的校验未通过，已保留原目录', target_not_empty: '目标目录包含现有数据，已保留原目录', target_not_directory: '目标路径不是可用目录，已保留原目录', overlapping_directories: '新目录与当前目录互相包含', same_directory: '新旧目录相同' }
  } : {
    moving: 'Moving app data…', done: 'Data migration complete', failed: 'Data migration incomplete',
    preparing: 'Preparing', scanning: 'Scanning files…', copying: 'Copying data…', verifying: 'Verifying…',
    items: ' items', continue: 'Continue with original folder', close: 'Close', cancel: 'Cancel migration and quit', quit: 'Quit app', failure: 'Migration failed: ', unknown: 'unknown error',
    errors: { verification_failed: 'Copy verification failed; the original folder was kept', target_not_empty: 'The target folder contains data; the original folder was kept', target_not_directory: 'The target path is not a usable folder', overlapping_directories: 'The new folder overlaps the current one', same_directory: 'The new and current folders are the same' }
  };
  const title = document.getElementById('title');
  const fill = document.getElementById('fill');
  const status = document.getElementById('status');
  const detail = document.getElementById('detail');
  const action = document.getElementById('action');
  let stopped = false;
  title.textContent = text.moving;
  status.textContent = text.preparing;
  action.style.display = 'block';
  action.textContent = text.cancel;

  function render(progress) {
    const total = progress.bytes_total || 0;
    const done = progress.bytes_done || 0;
    const percent = total ? Math.min(100, Math.round(done / total * 100)) : 0;
    if (progress.phase === 'scanning' || (progress.phase === 'copying' && !total)) {
      fill.classList.add('busy');
      fill.style.width = '';
    } else {
      fill.classList.remove('busy');
      fill.style.width = percent + '%';
    }
    status.textContent = ({ idle: text.preparing, scanning: text.scanning, copying: text.copying, verifying: text.verifying, done: text.done })[progress.phase] || progress.phase;
    detail.textContent = total && progress.phase !== 'error' ? percent + '% · ' + (progress.files_done || 0) + '/' + (progress.files_total || 0) + text.items : '';
    if (progress.phase === 'error') {
      stopped = Boolean(progress.backend_ready);
      title.textContent = text.failed;
      status.className = 'error';
      status.textContent = text.errors[progress.error] || text.failure + (progress.error || text.unknown);
      action.style.display = 'block';
      action.textContent = progress.backend_ready ? text.continue : text.quit;
    } else if (progress.phase === 'done') {
      stopped = true;
      title.textContent = text.done;
      fill.classList.remove('busy');
      fill.style.width = '100%';
      action.style.display = 'block';
      action.textContent = text.close;
    }
  }

  function poll() {
    fetch('/rundata/migration/progress', { cache: 'no-store' })
      .then((response) => response.json())
      .then((progress) => {
        render(progress);
        if (!stopped || (progress.phase === 'error' && !progress.backend_ready)) setTimeout(poll, 200);
      })
      .catch(() => setTimeout(poll, 400));
  }
  action.addEventListener('click', () => {
    action.disabled = true;
    fetch('/rundata/migration/continue', { method: 'POST' }).catch(() => {});
  });
  poll();
</script>
</body>
</html>`;

export async function createMigrationWindow(bridgePort) {
  const window = new BrowserWindow({
    width: 460,
    height: 190,
    title: 'Astrion',
    resizable: false,
    minimizable: false,
    maximizable: false,
    closable: true,
    alwaysOnTop: true,
    show: false,
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  });
  window.center();
  await window.loadURL(`http://127.0.0.1:${bridgePort}/rundata/migration`);
  window.show();
  return window;
}
