export const SCENE_PROGRESS_LABELS: Record<string, string> = {
  browserSearch: '正在搜索',
  webExtract: '正在提取',
  webSave: '正在保存',
  appendFile: '正在编辑',
  modifyFile: '正在编辑',
  createFile: '正在创建',
  deleteFile: '正在删除',
  readFile: '正在读取',
  reader: '正在读取',
  focus: '正在聚焦',
  unfocus: '正在处理',
  // 运行类工具显示具体工具名，由运行时传入
  runCommand: '运行命令',
  terminalSession: '打开终端',
  terminalInput: '终端输入',
  terminalSnapshot: '获取终端输出',
  memoryUpdate: '正在同步记忆',
  todoCreate: '正在更新待办',
  todoUpdate: '正在更新待办',
  todoFinish: '正在完成任务',
  todoFinishConfirm: '正在确认任务',
  todoDelete: '正在移除任务',
  wait: '正在等待',
  sleep: '正在等待',
  createFolder: '正在创建文件夹',
  renameFile: '正在重命名',
  terminalReset: '正在重置终端',
  terminalSleep: '准备等待',
  terminalRun: '终端运行中',
  ocr: '正在提取',
  memory: '正在同步记忆',
  todo: '正在管理待办',
  genericTool: '调用工具'
};

export function getSceneProgressLabel(name: string): string | null {
  return SCENE_PROGRESS_LABELS[name] || null;
}
