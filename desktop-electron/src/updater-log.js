import fs from 'node:fs';
import path from 'node:path';

const MAX_BYTES = 1024 * 1024;
const BACKUPS = 3;

/** GUI 应用没有可见控制台；关键更新事件同步落盘，退出前不会丢失。 */
export function createUpdaterLogger(app) {
  const logPath = path.join(app.getPath('userData'), 'logs', 'desktop-update.log');
  return (event, details = {}) => {
    try {
      fs.mkdirSync(path.dirname(logPath), { recursive: true });
      if (fs.existsSync(logPath) && fs.statSync(logPath).size >= MAX_BYTES) {
        for (let index = BACKUPS; index >= 1; index -= 1) {
          const source = index === 1 ? logPath : `${logPath}.${index - 1}`;
          const target = `${logPath}.${index}`;
          if (fs.existsSync(source)) fs.renameSync(source, target);
        }
      }
      fs.appendFileSync(logPath, `${JSON.stringify({
        time: new Date().toISOString(),
        pid: process.pid,
        version: app.getVersion(),
        event,
        ...details
      })}\n`);
    } catch (error) {
      // 日志路径不可写不能阻断更新；只打印一次可复制的字符串。
      console.warn(`[astrion-desktop-update] ${event}: ${String(error?.message || error)}`);
    }
  };
}
