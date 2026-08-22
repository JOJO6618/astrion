type PanelKey = 'left' | 'right' | 'conversation';

interface ResizeContext {
  isResizing?: boolean;
  resizingPanel?: PanelKey | null;
  rightCollapsed?: boolean;
  terminalPanelOpen?: boolean;
  gitChangesPanelOpen?: boolean;
  rightWidth?: number;
  minPanelWidth?: number;
  maxPanelWidth?: number;
  sidebarCollapsed?: boolean;
  handleResize?: (event: MouseEvent) => void;
  stopResize?: () => void;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(value, max));
}

export function startResize(ctx: ResizeContext, panel: PanelKey, event: MouseEvent) {
  // 右侧面板（终端 / Git）只有在至少一个打开时才允许拖拽调整宽度
  if (panel === 'right' && !ctx.terminalPanelOpen && !ctx.gitChangesPanelOpen) {
    return;
  }

  ctx.isResizing = true;
  ctx.resizingPanel = panel;

  if (ctx.handleResize) {
    document.addEventListener('mousemove', ctx.handleResize as EventListener);
  }
  if (ctx.stopResize) {
    document.addEventListener('mouseup', ctx.stopResize as EventListener);
  }
  document.body.style.userSelect = 'none';
  document.body.style.cursor = 'col-resize';
  event.preventDefault();
}

export function handleResize(ctx: ResizeContext, event: MouseEvent) {
  if (!ctx.isResizing || !ctx.resizingPanel) {
    return;
  }
  const containerEl = document.querySelector<HTMLElement>('.main-container');
  if (!containerEl) {
    return;
  }
  const containerWidth = containerEl.offsetWidth;
  const minWidth = ctx.minPanelWidth ?? 240;
  const maxWidth = ctx.maxPanelWidth ?? 480;

  if (ctx.resizingPanel === 'right') {
    let newWidth = containerWidth - event.clientX;
    newWidth = clamp(newWidth, minWidth, maxWidth);
    ctx.rightWidth = newWidth;
  } else if (ctx.resizingPanel === 'conversation') {
    // 目前对话侧栏宽度仅做边界控制，未暴露到 store
    clamp(event.clientX, 200, 400);
  }
}

export function stopResize(ctx: ResizeContext) {
  ctx.isResizing = false;
  ctx.resizingPanel = null;
  if (ctx.handleResize) {
    document.removeEventListener('mousemove', ctx.handleResize as EventListener);
  }
  if (ctx.stopResize) {
    document.removeEventListener('mouseup', ctx.stopResize as EventListener);
  }
  document.body.style.userSelect = '';
  document.body.style.cursor = '';
}
