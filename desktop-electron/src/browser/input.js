const MODIFIERS = new Set(['shift', 'control', 'alt', 'meta', 'leftButtonDown', 'middleButtonDown', 'rightButtonDown']);
const BUTTONS = ['left', 'middle', 'right'];

// Only the trusted, currently raised main renderer can relay input through its
// transparent page region. The destination is always the active registered tab.
export function relayBrowserInput(manager, payload) {
  const tab = manager.active();
  if (!manager.overlayRaised || !manager.visible || !manager.geometry || !tab?.view ||
    payload.tabId !== tab.id || tab.owner !== manager.owner || !tab.view.getVisible() ||
    tab.view.webContents.isDestroyed()) return;
  if (!['mouseDown', 'mouseUp', 'mouseMove', 'mouseWheel'].includes(payload.type) ||
    !Number.isFinite(payload.x) || !Number.isFinite(payload.y)) return;
  const zoom = manager.mainView.webContents.getZoomFactor();
  const bounds = tab.view.getBounds();
  const main = manager.mainView.getBounds();
  const relativeX = payload.x * zoom + main.x - bounds.x;
  const relativeY = payload.y * zoom + main.y - bounds.y;
  const inside = relativeX >= 0 && relativeY >= 0 && relativeX < bounds.width && relativeY < bounds.height;
  if (!inside && !(manager.relayPointer === tab.id && ['mouseUp', 'mouseMove'].includes(payload.type))) return;
  const event = {
    type: payload.type,
    x: Math.round(Math.max(0, Math.min(bounds.width - 1, relativeX))),
    y: Math.round(Math.max(0, Math.min(bounds.height - 1, relativeY))),
    modifiers: Array.isArray(payload.modifiers) ? payload.modifiers.filter(value => MODIFIERS.has(value)) : []
  };
  if (payload.type === 'mouseWheel') {
    if (!Number.isFinite(payload.deltaX) || !Number.isFinite(payload.deltaY)) return;
    event.deltaX = Math.max(-10000, Math.min(10000, -payload.deltaX * zoom));
    event.deltaY = Math.max(-10000, Math.min(10000, -payload.deltaY * zoom));
    event.hasPreciseScrollingDeltas = true;
    event.canScroll = true;
  } else {
    event.button = BUTTONS[payload.button] || 'left';
    event.clickCount = Math.max(1, Math.min(3, Number(payload.clickCount) || 1));
    if (payload.type === 'mouseDown') { manager.relayPointer = tab.id; tab.view.webContents.focus(); }
    if (payload.type === 'mouseUp') manager.relayPointer = null;
  }
  tab.view.webContents.sendInputEvent(event);
}
