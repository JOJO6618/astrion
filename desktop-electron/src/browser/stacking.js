export class BrowserStacking {
  constructor(manager) { this.manager = manager; this.raised = false; this.transparent = false; this.background = null; this.capturing = false; this.restoreFocus = null; this.findOpen = false; this.findFocus = null; }
  apply(active) {
    const manager = this.manager;
    const pageVisible = !!active?.view && !active.view.webContents.isDestroyed() && active.view.getVisible();
    const raised = pageVisible && manager.overlayRaised;
    const capturing = raised && manager.overlayCapturesInput !== false;
    const findOpen = pageVisible && manager.focusFind === true;
    if (findOpen && !this.findOpen) {
      this.findFocus = { id: active.id, focused: active.view.webContents.isFocused() };
      manager.mainView.webContents.focus();
    } else if (!findOpen && this.findOpen) {
      if (!capturing && pageVisible && this.findFocus?.focused && this.findFocus.id === active.id && manager.mainView.webContents.isFocused()) active.view.webContents.focus();
      this.findFocus = null;
    }
    this.findOpen = findOpen;
    if (capturing && !this.capturing) {
      this.restoreFocus = { id: active.id, focused: active.view.webContents.isFocused() };
      manager.mainView.webContents.focus();
    }
    const target = pageVisible && !raised ? active.view : manager.mainView;
    if (manager.window.contentView.children.at(-1) !== target) manager.window.contentView.addChildView(target);
    const background = pageVisible ? '#00000000' : manager.surfaceColor;
    if (background !== this.background) {
      manager.mainView.setBackgroundColor(background);
      this.background = background;
    }
    this.transparent = pageVisible;
    if (!capturing && this.capturing) {
      if (pageVisible && this.restoreFocus?.focused && this.restoreFocus.id === active.id) active.view.webContents.focus();
      this.restoreFocus = null;
    }
    this.raised = raised;
    this.capturing = capturing;
    if (!raised) manager.relayPointer = null;
    manager.contextMenu.closeStale();
  }
}
