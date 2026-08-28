// 监视器场景进行状态文案（progressMap）
// ⚠️ 顶层映射表只存文案 key（模块顶层禁止调 t()），getSceneProgressLabel 调用时 t(key) 解析，
//    语言切换后新产生的标签自动取当前语言；历史快照标签保持事件发生时语言（与迁移前一致）。
// ⚠️ 与 stores/monitor.ts 的拼接耦合：stores 侧 transformStatus 会对以「正在」开头的 zh 文案
//    执行 slice(2) 后拼装 t('stores.playbackStatus')（回放{label}）。
//    因此 zh-CN/chat.ts 的 progress* 译文必须保持「正在…」前缀结构；英文不命中前缀即原样透传。
import { t } from '@/locales';

export const SCENE_PROGRESS_LABELS: Record<string, string> = {
  browserSearch: 'chat.progressBrowserSearch',
  webExtract: 'chat.progressWebExtract',
  webSave: 'chat.progressWebSave',
  appendFile: 'chat.progressAppendFile',
  modifyFile: 'chat.progressModifyFile',
  createFile: 'chat.progressCreateFile',
  deleteFile: 'chat.progressDeleteFile',
  readFile: 'chat.progressReadFile',
  reader: 'chat.progressReader',
  focus: 'chat.progressFocus',
  unfocus: 'chat.progressUnfocus',
  // 运行类工具显示具体工具名，由运行时传入
  runCommand: 'chat.progressRunCommand',
  terminalSession: 'chat.progressTerminalSession',
  terminalInput: 'chat.progressTerminalInput',
  terminalSnapshot: 'chat.progressTerminalSnapshot',
  memoryUpdate: 'chat.progressMemoryUpdate',
  todoCreate: 'chat.progressTodoCreate',
  todoUpdate: 'chat.progressTodoUpdate',
  todoFinish: 'chat.progressTodoFinish',
  todoFinishConfirm: 'chat.progressTodoFinishConfirm',
  todoDelete: 'chat.progressTodoDelete',
  wait: 'chat.progressWait',
  sleep: 'chat.progressSleep',
  createFolder: 'chat.progressCreateFolder',
  renameFile: 'chat.progressRenameFile',
  terminalReset: 'chat.progressTerminalReset',
  terminalSleep: 'chat.progressTerminalSleep',
  terminalRun: 'chat.progressTerminalRun',
  ocr: 'chat.progressOcr',
  memory: 'chat.progressMemory',
  todo: 'chat.progressTodo',
  genericTool: 'chat.progressGenericTool'
};

export function getSceneProgressLabel(name: string): string | null {
  const key = SCENE_PROGRESS_LABELS[name];
  if (!key) return null;
  return t(key);
}