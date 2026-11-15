// 每个宿主窗口各自的叠层：网页可见时宿主 renderer 透明铺底，DOM 浮层出现时把宿主抬到网页之上。
export class BrowserStacking {
  constructor(host) { this.host = host; this.raised = false; this.transparent = false; this.background = null; this.capturing = false; this.restoreFocus = null; this.findOpen = false; this.findFocus = null; }
  /** 只在宿主窗口已是焦点窗口时移动键盘焦点：macOS 上 WebContents.focus() 会连带激活窗口/应用，
   *  周期性路径（布局、快照收敛）不得因此抢走用户正在使用的其他应用的焦点。 */
  focus(wc) { if (this.host.window.isFocused() && !wc.isDestroyed()) wc.focus(); }
  apply(active) {
    const host = this.host;
    if (host.window.isDestroyed() || host.view.webContents.isDestroyed()) return;
    // 隐藏的内容视图不参与叠层：否则会把它（或它的网页视图）抬到当前显示的视图之上。
    if (!host.presented) return;
    const pageVisible = !!active?.view && !active.view.webContents.isDestroyed() && active.view.getVisible();
    const raised = pageVisible && host.overlayRaised;
    const capturing = raised && host.overlayCapturesInput !== false;
    const findOpen = pageVisible && host.focusFind === true;
    if (findOpen && !this.findOpen) {
      this.findFocus = { id: active.id, focused: active.view.webContents.isFocused() };
      this.focus(host.view.webContents);
    } else if (!findOpen && this.findOpen) {
      if (!capturing && pageVisible && this.findFocus?.focused && this.findFocus.id === active.id && host.view.webContents.isFocused()) this.focus(active.view.webContents);
      this.findFocus = null;
    }
    this.findOpen = findOpen;
    if (capturing && !this.capturing) {
      this.restoreFocus = { id: active.id, focused: active.view.webContents.isFocused() };
      this.focus(host.view.webContents);
    }
    const target = pageVisible && !raised ? active.view : host.view;
    if (host.window.contentView.children.at(-1) !== target) host.window.contentView.addChildView(target);
    const background = pageVisible ? '#00000000' : host.manager.surfaceColor;
    if (background !== this.background) {
      host.view.setBackgroundColor(background);
      this.background = background;
    }
    this.transparent = pageVisible;
    if (!capturing && this.capturing) {
      if (pageVisible && this.restoreFocus?.focused && this.restoreFocus.id === active.id) this.focus(active.view.webContents);
      this.restoreFocus = null;
    }
    this.raised = raised;
    this.capturing = capturing;
    if (!raised) host.relayPointer = null;
  }
}
