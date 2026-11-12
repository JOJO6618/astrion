// 浏览器宿主：一个承载浏览器界面的可信 renderer（主窗口，或某个对话弹出的独立窗口）
// 及其连接状态。标签与资料由 NativeBrowser 统一持有；宿主只描述“谁在显示哪个对话、
// 显示在哪里”。主窗口宿主跟随当前对话切换；独立窗口宿主的对话在弹出时由主进程固定。
import { browserRoute, trustedFrame } from './policy.js';
import { BrowserStacking } from './stacking.js';

export const POPOUT_ROUTE = '/browser-window';

export class BrowserHost {
  constructor(manager, { kind, window, view, owner = '', profile = null }) {
    Object.assign(this, {
      manager, kind, window, view, owner, profile,
      lease: '', visible: false, geometry: null, geometryViewport: null,
      overlayRaised: false, overlayCapturesInput: true, focusFind: false, relayPointer: null,
      menuLabels: undefined, layoutSequence: 0, connectSequence: 0
    });
    this.stacking = new BrowserStacking(this);
  }
  get popout() { return this.kind === 'popout'; }
  /** 主窗口当前对话已弹出到独立窗口：主窗口只显示占位，不显示也不操作该对话的标签。 */
  get detached() { return !this.popout && !!this.owner && this.manager.popouts.has(this.owner); }
  route(pathname) { return this.popout ? pathname === POPOUT_ROUTE : browserRoute(pathname); }
  trusted(event) { return trustedFrame(event, this.view, this.manager.origin, pathname => this.route(pathname)); }
  active() { return this.detached ? undefined : this.manager.tabs.get(this.manager.selected.get(this.owner)); }
  send(channel, data) {
    const wc = this.view?.webContents;
    if (!this.manager.disposed && wc && !wc.isDestroyed()) wc.send(channel, data);
  }
  message(message) { this.send('browser:message', { lease: this.lease, message }); }
}
