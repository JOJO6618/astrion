// ViewManager：主窗口内多个内容视图（每标签一个 WebContentsView）的生命周期与显示。
// 不直接依赖 Electron：窗口、chrome 视图与视图工厂都由调用方注入，便于用伪对象单测。
// 后端 modules/desktop_navigation/ 是标签集合/顺序/归属的唯一写者；这里只按快照收敛。
import { createEntry, isLegacy, LEGACY_KEY } from './entry.js';
import { mainContentBounds, chromeBounds, detachedContentBounds, DETACHED_BAR_HEIGHT, toInt } from './layout.js';
import {
  acceptSnapshot, applyPendingPlacement, buildViewPath, chooseEvictions, guardedActive, isMainPlacement,
  MAX_RESIDENT_VIEWS, planReconcile, successorKey, warmupOrder
} from './plan.js';

const LEGACY_DELAY_MS = 3000;
const WARMUP_STEP_MS = 1500;
const RECENCY_SAVE_MS = 1000;
const OPTIMISTIC_MS = 1500; // 乐观显示后保护期：期间旧意图的快照不得切走可见视图
const PENDING_PLACEMENT_MS = 2500; // detach/dock 命令等待后端确认的时限；超时仍不一致则以快照为准
const DRAG_IDLE_MS = 30000; // 拖出态下这么久没有任何拖拽消息：视为 drop
const DRAG_POLL_MS = 16; // 拖出态光标轮询（chrome 的 move 消息中断时的兜底）
const DRAG_POLL_STALE_MS = 120; // 超过该时长没收到 move 才由轮询接管
const CRASH_WINDOW_MS = 60000;
const CRASH_LIMIT = 3; // 窗口期内最多自动重建次数，超过则放弃（避免崩溃循环）
const alive = (view) => !!view && !view.webContents.isDestroyed();

export class ViewManager {
  /**
   * @param {object} deps
   * @param {string} deps.origin 后端 origin（http://127.0.0.1:<port>）
   * @param {object} deps.window 主窗口（BaseWindow 形：contentView.add/removeChildView、getContentBounds、isDestroyed）
   * @param {object} deps.chromeView 顶部标签条视图
   * @param {(spec:{key:string,url:string,legacy:boolean})=>object} deps.createView 创建并开始加载内容视图（初始隐藏）
   * @param {(spec:{key:string,point:object|null,focus:boolean},callbacks:object)=>object} [deps.createDetachedWindow]
   *   创建独立对话窗口（views/detached-window.js）；callbacks: onClose/onFocus/onResize/onVisibility
   * @param {() => boolean} [deps.isQuitting] 应用正在退出/安装更新（独立窗口此时关闭不得放回、不得请求 dock，以便下次启动恢复）
   * @param {() => ({x:number,y:number}|null)} [deps.getCursor] 拖出落点兜底（screen.getCursorScreenPoint）
   * @param {{onViewCreated?:Function,onViewDestroyed?:Function,onPresentationChanged?:Function,onViewMoved?:Function,onViewResized?:Function}} [deps.hooks]
   * @param {() => Record<string,number>} [deps.loadRecency]
   * @param {(value:Record<string,number>) => void} [deps.saveRecency]
   * @param {{set:Function,clear:Function}} [deps.timers]
   * @param {() => number} [deps.now]
   */
  constructor({ origin, window, chromeView, createView, createDetachedWindow, getCursor, isQuitting = () => false, hooks = {}, loadRecency, saveRecency,
    timers = defaultTimers, now = Date.now, maxViews = MAX_RESIDENT_VIEWS, legacyDelay = LEGACY_DELAY_MS, warmupStep = WARMUP_STEP_MS }) {
    Object.assign(this, { origin, window, chromeView, createView, createDetachedWindow, getCursor, isQuitting, hooks, saveRecency, timers, now, maxViews, legacyDelay, warmupStep });
    /** @type {Map<string, import('./entry.js').Entry>} */
    this.entries = new Map();
    /** 独立对话窗口：key → BaseWindow（位置记忆在 detached-window.js）。 */
    this.detachedWindows = new Map();
    /** 乐观显示保护 {key, until}；detach/dock 待后端确认的 key → 截止时间。 */
    this.optimistic = null;
    this.pendingDock = new Map();
    this.pendingDetach = new Map();
    /** key → 最近崩溃时间戳（崩溃重建限流）。 */
    this.crashes = new Map();
    /** 进行中的拖出（tear 之后到 drop/cancel 之前）：{key, prevActive, offset, lastPoint, lastMoveAt, idle, poll}。 */
    this.drag = null;
    this.snapshot = null;
    this.revision = -1;
    this.activeKey = '';
    this.sidebarWidth = 0;
    this.chromeOverlay = false;
    this.waitingForLogin = false;
    this.browserPanels = new Map();
    /** 首个快照到达前收到的“打开页面”请求（如快捷对话设置）；之后经 chrome 发 `page` 命令。 */
    this.queuedRoutes = [];
    this.disposed = false;
    this.warmupStarted = false;
    this.recency = loadRecency ? { ...loadRecency() } : {};
    this.timerIds = new Set();
    this.recencyTimer = null;
    this.legacyTimer = this.after(this.legacyDelay, () => this.showLegacy());
  }

  // ---- 查询 ----
  /** 真实标签对应的条目（不含 legacy 兜底视图）。 */
  realEntries() { return [...this.entries.values()].filter((entry) => !isLegacy(entry)); }
  getActiveEntry() { return this.entries.get(this.activeKey) || null; }
  getActiveView() { return this.getActiveEntry()?.view || null; }
  entryForContents(wc) { return [...this.entries.values()].find((entry) => entry.view.webContents === wc) || null; }
  isManagedView(wc) { return !!this.entryForContents(wc); }
  isChrome(wc) { return !!this.chromeView && this.chromeView.webContents === wc; }
  /** 当前快照中指定 key 的标签（不存在返回 null）。 */
  tabForKey(key) { return this.snapshot?.tabs.find((tab) => tab.key === key) || null; }
  isShown(view) { return [...this.entries.values()].some((entry) => entry.view === view && entry.shown); }
  detachedWindowOf(key) { return this.detachedWindows.get(key) || null; }
  windowOf(view) { return [...this.entries.values()].find((entry) => entry.view === view)?.window || null; }
  /** identity：按 sender 返回，页面不得自报。legacy 兜底视图 key 为空。 */
  identityFor(wc) {
    const entry = this.entryForContents(wc);
    if (!entry) return null;
    return { key: isLegacy(entry) ? '' : entry.key, placement: entry.placement, visible: entry.shown, legacy: isLegacy(entry), state: this.snapshot };
  }

  // ---- 快照 ----
  /** 接受快照（仅 revision 更大者）、收敛并广播；source 为转发者 webContents（不回显给它）。 */
  handleSnapshot(state, source = null) {
    if (this.disposed || this.waitingForLogin || !acceptSnapshot(this.revision, state)) return false;
    this.revision = state.revision;
    this.snapshot = state;
    const retained = new Set(state.tabs.map((tab) => tab.key));
    for (const key of this.browserPanels.keys()) if (!retained.has(key)) this.browserPanels.delete(key);
    this.reconcile();
    this.broadcast(state, source);
    this.flushQueuedRoutes();
    return true;
  }
  broadcast(state, except = null) {
    const targets = [this.chromeView, ...[...this.entries.values()].map((entry) => entry.view)];
    for (const view of targets) {
      const wc = view?.webContents;
      if (!wc || wc.isDestroyed() || wc === except) continue;
      wc.send('shell:tabs-state', state);
    }
  }
  /** 主题变更广播：隐藏视图挂着不卸载，必须主动通知它们重设 data-theme。 */
  broadcastTheme(theme, except = null) {
    const targets = [this.chromeView, ...[...this.entries.values()].map((entry) => entry.view)];
    for (const view of targets) {
      const wc = view?.webContents;
      if (!wc || wc.isDestroyed() || wc === except) continue;
      wc.send('shell:theme-changed', theme);
    }
  }

  // ---- 收敛 ----
  reconcile() {
    const raw = this.snapshot;
    if (!raw || this.disposed) return;
    this.dropLegacy();
    const nowMs = this.now();
    // 先行处理的 detach/dock 尚未被后端确认时，把它们叠加到快照上，避免旧快照把视图挪回去。
    const { state, satisfied } = applyPendingPlacement(raw, { dock: new Set(this.pendingDock.keys()), detach: new Set(this.pendingDetach.keys()) });
    for (const key of satisfied.dock) this.pendingDock.delete(key);
    for (const key of satisfied.detach) this.pendingDetach.delete(key);
    for (const map of [this.pendingDock, this.pendingDetach]) {
      for (const [key, until] of map) if (nowMs >= until) map.delete(key);
    }
    const tabs = new Map(raw.tabs.map((tab) => [tab.key, tab]));
    this.pruneRecency(tabs);
    // 乐观显示保护：快照已指向该标签（或标签不再属于主窗口/保护过期）即解除。
    let forced = guardedActive(state, this.optimistic, nowMs);
    if (!forced) this.optimistic = null;
    // 拖出进行中：显示的主窗口标签保持不变（后端尚不知道 detach，旧快照的 active 可能还是被拖标签）。
    if (!forced && this.drag && this.activeKey && isMainPlacement(tabs.get(this.activeKey))) forced = this.activeKey;
    const plan = planReconcile(state, this.realEntries(), { active: forced });
    for (const key of plan.destroy) this.destroyEntry(key);
    for (const key of plan.toMain) this.moveToMain(this.entries.get(key));
    for (const key of plan.toDetached) this.moveToDetached(this.entries.get(key), { focus: false });
    for (const key of plan.createDetached) {
      const tab = tabs.get(key);
      if (tab?.kind === 'conv' || tab?.kind === 'new') this.moveToDetached(this.createForTab(tab), { focus: false });
    }
    for (const key of plan.hide) this.hideEntry(this.entries.get(key));
    if (!plan.active) { this.activeKey = ''; this.evict(); return; }
    const entry = this.entries.get(plan.active) || this.createForTab(tabs.get(plan.active));
    this.display(entry);
    this.evict();
    if (!this.warmupStarted) { this.warmupStarted = true; this.scheduleWarmup(); }
  }
  /** 乐观切换：chrome 点击标签先显示（缺失则立即创建），随后以后端快照为准再收敛。
   *  保护期内旧意图的快照不会切走它（避免闪回）。 */
  show(key, requestId) {
    if (this.disposed || !this.snapshot || typeof key !== 'string') return false;
    const tab = this.snapshot.tabs.find((row) => row.key === key);
    if (!tab || !isMainPlacement(tab)) return false;
    this.dropLegacy();
    this.display(this.entries.get(key) || this.createForTab(tab));
    this.evict();
    if (requestId) this.optimistic = { key, requestId };
    else this.guard(key);
    return true;
  }
  settleShow(requestId) {
    if (!requestId || this.optimistic?.requestId !== requestId) return;
    this.optimistic = null;
    this.reconcile();
  }
  guard(key) {
    const until = this.now() + OPTIMISTIC_MS;
    const guard = { key, until };
    this.optimistic = guard;
    this.after(OPTIMISTIC_MS, () => { if (this.optimistic === guard) { this.optimistic = null; this.reconcile(); } });
  }
  /** 把 entry 作为主窗口当前显示的视图：其余主窗口视图隐藏。 */
  display(entry, { focus = true } = {}) {
    for (const other of this.entries.values()) if (other !== entry && other.placement !== 'detached') this.hideEntry(other);
    const switched = this.activeKey !== entry.key;
    const wasShown = entry.shown;
    this.activeKey = entry.key;
    entry.view.setBounds(this.mainBounds(entry.kind));
    entry.view.setVisible(true);
    if (!entry.shown || switched) {
      entry.lastShown = this.now();
      this.recency[entry.key] = entry.lastShown;
      this.scheduleRecencySave();
    }
    if (!entry.shown) this.setShown(entry, true);
    // 键盘焦点只在“切换到它/刚显示”时移入，且仅当所在窗口已是焦点窗口：
    // display 会在每次快照收敛时调用，WebContents.focus() 在 macOS 上还会激活窗口/应用（抢焦点）。
    if (focus && !this.chromeOverlay && (switched || !wasShown) && alive(entry.view) && entry.window?.isFocused?.()) entry.view.webContents.focus();
    this.hooks.onPresentationChanged?.();
    this.restoreChromeOverlay();
  }
  hideEntry(entry) {
    if (!entry) return;
    if (alive(entry.view)) entry.view.setVisible(false);
    if (entry.shown) this.setShown(entry, false);
    this.hooks.onPresentationChanged?.();
  }
  setShown(entry, visible) {
    entry.shown = visible;
    if (alive(entry.view)) entry.view.webContents.send('shell:visibility', { visible });
  }
  evict() {
    for (const key of chooseEvictions(this.realEntries(), this.activeKey, this.maxViews)) this.destroyEntry(key);
  }

  // ---- 视图创建/销毁 ----
  createForTab(tab) {
    return this.attachView({ key: tab.key, kind: tab.kind, url: `${this.origin}${buildViewPath(tab)}`, lastShown: Number(this.recency[tab.key]) || 0 });
  }
  attachView({ key, kind, url, lastShown }) {
    const view = this.createView({ key, url, legacy: kind === 'legacy' });
    view.setVisible(false);
    view.setBounds(this.mainBounds(kind));
    this.window.contentView.addChildView(view);
    const entry = createEntry({ key, kind, view, window: this.window, url, lastShown });
    this.entries.set(key, entry);
    view.webContents.on?.('render-process-gone', (_event, details) => {
      if (details?.reason !== 'clean-exit' && this.entries.get(key) === entry) this.handleCrash(key);
    });
    this.hooks.onViewCreated?.(entry);
    return entry;
  }
  destroyEntry(key, { keepWindow = false } = {}) {
    const entry = this.entries.get(key);
    if (!entry) return;
    this.entries.delete(key);
    if (this.activeKey === key) this.activeKey = '';
    if (this.drag?.key === key) this.clearDrag();
    // 先退役浏览器宿主（其网页标签停放到主窗口），再关闭视图。
    try { this.hooks.onViewDestroyed?.(entry); } catch (err) { console.error('[astrion-desktop] 视图销毁回调失败:', err); }
    const window = entry.window;
    if (window && !window.isDestroyed?.()) { try { window.contentView.removeChildView(entry.view); } catch { /* 已移除 */ } }
    if (alive(entry.view)) entry.view.webContents.close();
    if (entry.placement === 'detached' && !keepWindow) this.destroyDetachedWindow(key);
    this.hooks.onPresentationChanged?.();
  }

  // ---- 独立对话窗口（P2，设计 §4.6/§4.7） ----
  /** 把视图迁入该标签的独立窗口（不存在则创建；视图 removeChildView → addChildView，不重载）。
   *  重启恢复与拖出都走这里。 */
  moveToDetached(entry, { point = null, offset = null, focus = true } = {}) {
    if (!entry || this.disposed || !this.createDetachedWindow) return false;
    let window = this.detachedWindows.get(entry.key);
    if (!window || window.isDestroyed?.()) {
      window = this.createDetachedWindow({ key: entry.key, point, offset, focus }, {
        onClose: () => this.handleDetachedClose(entry.key),
        // 拖出期间窗口被点击获得焦点：用户已在别处操作，按落下处理。
        onFocus: () => { if (this.drag?.key === entry.key) this.dropTorn(); else this.sendChrome('shell:window-focus', { key: entry.key }); },
        onResize: () => this.layoutDetached(entry.key),
        onVisibility: (visible) => this.detachedVisibility(entry.key, visible)
      });
      this.detachedWindows.set(entry.key, window);
    }
    const from = entry.window;
    if (from && from !== window && !from.isDestroyed?.()) { try { from.contentView.removeChildView(entry.view); } catch { /* 未挂载 */ } }
    if (this.activeKey === entry.key) this.activeKey = '';
    if (alive(entry.view)) {
      window.contentView.addChildView(entry.view);
      entry.view.setBounds(detachedContentBounds(window.getContentBounds()));
      entry.view.setVisible(true);
    }
    entry.window = window;
    entry.placement = 'detached';
    const title = entry.view.webContents.getTitle?.();
    if (title) window.setTitle?.(title);
    if (!entry.shown) this.setShown(entry, true);
    this.notifyPlacement(entry);
    this.hooks.onViewMoved?.(entry, window);
    this.hooks.onPresentationChanged?.();
    return true;
  }
  /** 独立窗口里的视图放回主窗口（隐藏，保持存活，此后计入 LRU）并关闭独立窗口。 */
  moveToMain(entry) {
    if (!entry || entry.placement !== 'detached') return;
    const window = entry.window;
    this.detachedWindows.delete(entry.key);
    if (window && !window.isDestroyed?.()) { try { window.contentView.removeChildView(entry.view); } catch { /* 已移除 */ } }
    this.window.contentView.addChildView(entry.view);
    entry.window = this.window;
    entry.placement = 'main';
    entry.lastShown = this.now(); // 刚被查看过：放回后不应立刻成为 LRU 淘汰对象
    entry.view.setBounds(this.mainBounds(entry.kind));
    entry.view.setVisible(false);
    if (entry.shown) this.setShown(entry, false);
    this.notifyPlacement(entry);
    this.hooks.onViewMoved?.(entry, this.window);
    this.hooks.onPresentationChanged?.();
    if (window && !window.isDestroyed?.()) window.destroy(); // destroy 不触发 close 事件，不会重复请求 dock
  }
  /** 用户关闭独立窗口（close 事件，窗口仍在）：立即放回主窗口，再请 chrome 发 dock。 */
  handleDetachedClose(key) {
    // 退出/安装更新期间窗口被系统关闭：什么都不做，placement 保持 detached 以便下次启动恢复（D9）。
    if (this.disposed || this.isQuitting()) return;
    const entry = this.entries.get(key);
    const window = this.detachedWindows.get(key);
    this.detachedWindows.delete(key);
    if (!entry || entry.placement !== 'detached') return;
    if (window && !window.isDestroyed?.()) { try { window.contentView.removeChildView(entry.view); } catch { /* 已移除 */ } }
    this.window.contentView.addChildView(entry.view);
    entry.window = this.window;
    entry.placement = 'main';
    entry.lastShown = this.now(); // 刚被查看过：放回后不应立刻成为 LRU 淘汰对象
    entry.view.setBounds(this.mainBounds(entry.kind));
    entry.view.setVisible(false);
    if (entry.shown) this.setShown(entry, false);
    this.pendingDock.set(key, this.now() + PENDING_PLACEMENT_MS);
    this.after(PENDING_PLACEMENT_MS, () => { this.pendingDock.delete(key); this.reconcile(); });
    this.notifyPlacement(entry);
    this.hooks.onViewMoved?.(entry, this.window);
    this.evict();
    this.hooks.onPresentationChanged?.();
    this.sendChrome('shell:request-dock', { key });
  }
  destroyDetachedWindow(key) {
    const window = this.detachedWindows.get(key);
    this.detachedWindows.delete(key);
    if (window && !window.isDestroyed?.()) window.destroy();
  }
  layoutDetached(key) {
    const entry = this.entries.get(key);
    const window = this.detachedWindows.get(key);
    if (!entry || !window || window.isDestroyed?.() || !alive(entry.view)) return;
    entry.view.setBounds(detachedContentBounds(window.getContentBounds()));
    this.hooks.onViewResized?.(entry);
  }
  detachedVisibility(key, visible) {
    const entry = this.entries.get(key);
    if (entry && entry.placement === 'detached' && entry.shown !== visible) this.setShown(entry, visible);
    this.hooks.onPresentationChanged?.();
  }
  notifyPlacement(entry) {
    if (alive(entry.view)) entry.view.webContents.send('shell:placement', { key: entry.key, placement: entry.placement });
  }
  sendChrome(channel, data) {
    const wc = this.chromeView?.webContents;
    if (wc && !wc.isDestroyed()) wc.send(channel, data);
  }
  /** 拖出（设计 §4.6，浏览器式）：phase = tear（越过拖出阈值，立即建窗并跟随指针）/ move / drop / cancel。
   *  tear 之后窗口已存在但后端尚未 detach；drop 才请 chrome 发 detach。同一时刻只允许一次拖出。
   *  无 tear 直接 drop（旧 chrome）按原路径：落点建窗 + 请求 detach。 */
  dragTab(payload = {}) {
    const { key, phase } = payload;
    if (this.disposed || typeof key !== 'string') return false;
    if (phase === 'tear') return this.tear(payload);
    if (phase === 'move') return this.moveTorn(payload);
    if (phase === 'cancel') return this.cancelTorn(key);
    if (phase === 'drop') return this.drag?.key === key ? this.dropTorn(payload) : this.dropWithoutTear(payload);
    return false; // start 等其它阶段无动作
  }
  validDraggable(key) {
    if (!this.snapshot || !this.createDetachedWindow) return null;
    const tab = this.snapshot.tabs.find((row) => row.key === key);
    return tab && (tab.kind === 'conv' || tab.kind === 'new') && isMainPlacement(tab) && !this.pendingDetach.has(key) ? tab : null;
  }
  pointOf({ screenX, screenY } = {}) {
    return Number.isFinite(screenX) && Number.isFinite(screenY) ? { x: screenX, y: screenY } : (this.getCursor?.() || null);
  }
  /** tear：窗口按“指针位于窗口内 offset 处（顶部栏上）”建在光标下，不聚焦（不打断 chrome 的事件流），
   *  视图迁入（不重载），active 则乐观显示继任标签；pendingDetach 无超时，拖拽期间快照不会把它拉回。 */
  tear(payload) {
    const { key } = payload;
    if (this.drag || !this.validDraggable(key)) return false;
    const tab = this.snapshot.tabs.find((row) => row.key === key);
    const point = this.pointOf(payload);
    const offset = {
      x: Math.max(0, Number.isFinite(payload.offsetX) ? payload.offsetX : 120),
      y: Math.min(DETACHED_BAR_HEIGHT - 1, Math.max(0, Number.isFinite(payload.offsetY) ? payload.offsetY : DETACHED_BAR_HEIGHT / 2))
    };
    this.dropLegacy();
    const prevActive = this.activeKey;
    const successor = prevActive === key ? successorKey(this.snapshot, key) : '';
    const entry = this.entries.get(key) || this.createForTab(tab);
    if (!this.moveToDetached(entry, { point, offset, focus: false })) return false;
    const window = this.detachedWindows.get(key);
    if (window) window.astrionDragging = true; // 拖拽中的 moved 事件不写位置记忆
    this.pendingDetach.set(key, Infinity);
    if (successor) {
      const next = this.entries.get(successor) || this.createForTab(this.snapshot.tabs.find((row) => row.key === successor));
      this.display(next, { focus: false });
      this.guard(successor);
    }
    this.drag = { key, prevActive, offset, lastPoint: point, lastMoveAt: this.now(), idle: null, poll: null };
    this.armDrag();
    return true;
  }
  armDrag() {
    const drag = this.drag;
    if (!drag) return;
    if (drag.idle) { this.timers.clear(drag.idle); this.timerIds.delete(drag.idle); }
    drag.idle = this.after(DRAG_IDLE_MS, () => { drag.idle = null; if (this.drag === drag) this.dropTorn(); });
    if (!drag.poll) {
      const tick = () => {
        if (this.drag !== drag) return;
        // chrome 的 move 消息停了（例如指针捕获在新窗口出现后失效）：改由光标轮询驱动窗口位置。
        if (this.now() - drag.lastMoveAt > DRAG_POLL_STALE_MS) {
          const point = this.getCursor?.();
          if (point) this.positionTorn(point);
        }
        drag.poll = this.after(DRAG_POLL_MS, tick);
      };
      drag.poll = this.after(DRAG_POLL_MS, tick);
    }
  }
  clearDrag() {
    const drag = this.drag;
    this.drag = null;
    if (!drag) return;
    for (const id of [drag.idle, drag.poll]) if (id) { this.timers.clear(id); this.timerIds.delete(id); }
    const window = this.detachedWindows.get(drag.key);
    if (window) window.astrionDragging = false;
  }
  positionTorn(point) {
    const drag = this.drag;
    const window = drag && this.detachedWindows.get(drag.key);
    if (!window || window.isDestroyed?.() || !point) return;
    drag.lastPoint = point;
    // gin 拒绝 -0/NaN/小数（指针 y == 偏移 y 时差值为 -0）：先整数化，非有限数则跳过这次摆放。
    const x = toInt(point.x - drag.offset.x), y = toInt(point.y - drag.offset.y);
    if (x === null || y === null) return;
    try { window.setPosition?.(x, y); } catch (err) { console.error('[astrion-desktop] 拖拽窗口定位失败:', err); }
  }
  moveTorn(payload) {
    if (this.drag?.key !== payload.key) return false;
    const point = this.pointOf(payload);
    this.drag.lastMoveAt = this.now();
    this.positionTorn(point);
    this.armDrag();
    return true;
  }
  /** drop：最终位置、聚焦独立窗口与其视图、记住位置，并按正常路径请求 detach（带确认超时）。 */
  dropTorn(payload = {}) {
    const drag = this.drag;
    if (!drag) return false;
    const key = drag.key;
    if (Number.isFinite(payload.screenX) && Number.isFinite(payload.screenY)) this.positionTorn({ x: payload.screenX, y: payload.screenY });
    this.clearDrag();
    const entry = this.entries.get(key);
    const window = this.detachedWindows.get(key);
    if (!entry || entry.placement !== 'detached' || !window || window.isDestroyed?.()) { this.pendingDetach.delete(key); return false; }
    window.astrionRemember?.();
    window.show(); window.focus();
    if (alive(entry.view)) entry.view.webContents.focus();
    this.pendingDetach.set(key, this.now() + PENDING_PLACEMENT_MS);
    this.after(PENDING_PLACEMENT_MS, () => { this.pendingDetach.delete(key); this.reconcile(); });
    this.sendChrome('shell:request-detach', { key });
    return true;
  }
  /** cancel（指针回到主窗口标签条 / Esc）：视图放回主窗口、销毁拖拽窗口（不走关闭→dock，也不写位置记忆），
   *  恢复拖前显示的标签。 */
  cancelTorn(key) {
    const drag = this.drag;
    if (!drag || drag.key !== key) return false;
    this.clearDrag();
    this.pendingDetach.delete(key);
    const entry = this.entries.get(key);
    if (entry?.placement === 'detached') this.moveToMain(entry); // destroy() 不触发 close 事件
    const tab = this.snapshot?.tabs.find((row) => row.key === drag.prevActive);
    if (tab && isMainPlacement(tab)) {
      this.display(this.entries.get(tab.key) || this.createForTab(tab), { focus: false });
      this.guard(tab.key);
    }
    this.evict();
    return true;
  }
  /** chrome 异常（render-process-gone）时按 drop 处理。 */
  endDragAsDrop() { return this.dropTorn(); }
  /** 旧路径：没有 tear 直接 drop（落点建窗 + 乐观继任 + 请求 detach）。 */
  dropWithoutTear({ key, ...rest }) {
    if (this.drag || !this.validDraggable(key)) return false;
    const tab = this.snapshot.tabs.find((row) => row.key === key);
    const point = this.pointOf(rest);
    this.dropLegacy();
    const successor = this.activeKey === key ? successorKey(this.snapshot, key) : '';
    const entry = this.entries.get(key) || this.createForTab(tab);
    if (!this.moveToDetached(entry, { point, focus: true })) return false;
    this.pendingDetach.set(key, this.now() + PENDING_PLACEMENT_MS);
    this.after(PENDING_PLACEMENT_MS, () => { this.pendingDetach.delete(key); this.reconcile(); });
    if (successor) {
      const next = this.entries.get(successor) || this.createForTab(this.snapshot.tabs.find((row) => row.key === successor));
      this.display(next);
      this.guard(successor);
    }
    this.sendChrome('shell:request-detach', { key });
    return true;
  }
  /** 前置持有该标签的窗口：独立窗口则前置它；主窗口标签则乐观显示。 */
  focusTab(key) {
    const entry = this.entries.get(key);
    if (entry?.placement === 'detached') {
      const window = this.detachedWindows.get(key);
      if (!window || window.isDestroyed?.()) return false;
      if (window.isMinimized?.()) window.restore();
      window.show(); window.focus();
      if (alive(entry.view)) entry.view.webContents.focus();
      return true;
    }
    return this.show(key);
  }
  /** 快捷模式：主窗口隐藏/恢复时独立窗口一并隐藏/恢复。 */
  hideDetached() { for (const window of this.detachedWindows.values()) if (!window.isDestroyed?.()) window.hide(); }
  showDetached() {
    for (const window of this.detachedWindows.values()) {
      if (window.isDestroyed?.() || window.isVisible?.()) continue;
      if (window.showInactive) window.showInactive(); else window.show();
    }
  }
  /** 页面标题（document.title）同步到独立窗口标题。 */
  titleChanged(wc, title) {
    const entry = this.entryForContents(wc);
    if (entry?.placement === 'detached') entry.window?.setTitle?.(title);
  }

  // ---- 崩溃恢复 ----
  /** renderer 崩溃：销毁条目（退役浏览器宿主）；当前显示的/独立窗口里的视图按快照重建，其余冷加载时再说。
   *  重建带限流：窗口期内超过 CRASH_LIMIT 次就放弃，避免崩溃循环。 */
  handleCrash(key) {
    const entry = this.entries.get(key);
    if (!entry || this.disposed) return;
    const wasMainActive = this.activeKey === key && entry.placement === 'main';
    const wasDetached = entry.placement === 'detached';
    this.destroyEntry(key, { keepWindow: wasDetached });
    if (isLegacy(entry) || !(wasMainActive || wasDetached)) {
      if (wasDetached) this.destroyDetachedWindow(key);
      return;
    }
    const nowMs = this.now();
    const recent = (this.crashes.get(key) || []).filter((at) => nowMs - at < CRASH_WINDOW_MS);
    recent.push(nowMs);
    this.crashes.set(key, recent);
    if (recent.length > CRASH_LIMIT) {
      console.error(`[astrion-desktop] 视图 ${key} 在 ${CRASH_WINDOW_MS / 1000}s 内反复崩溃，不再自动重建`);
      if (wasDetached) this.destroyDetachedWindow(key);
      return;
    }
    const delay = recent.length <= 1 ? 0 : 1000 * 2 ** (recent.length - 2); // 退避：0、1s、2s
    const recreate = () => {
      if (this.disposed || this.entries.has(key)) return;
      const tab = this.snapshot?.tabs.find((row) => row.key === key);
      if (!tab) { this.destroyDetachedWindow(key); return; }
      if (wasDetached && !isMainPlacement(tab)) { this.moveToDetached(this.createForTab(tab), { focus: false }); return; }
      if (wasDetached) { this.destroyDetachedWindow(key); return; }
      if (isMainPlacement(tab) && !this.activeKey) { this.display(this.createForTab(tab)); this.evict(); }
    };
    if (delay === 0) recreate(); else this.after(delay, recreate);
  }

  // ---- legacy 兜底：未收到任何快照时的单页 `/`（前端尚未升级时避免空窗口） ----
  showLegacy(path = '') {
    this.legacyTimer = null;
    if (this.disposed || this.revision >= 0 || this.entries.has(LEGACY_KEY)) return;
    const target = path || this.queuedRoutes.pop() || '/';
    this.queuedRoutes = [];
    const entry = this.attachView({ key: LEGACY_KEY, kind: 'legacy', url: `${this.origin}${target}`, lastShown: 0 });
    this.display(entry);
    return entry.view;
  }
  beginLogin(path) {
    if (this.waitingForLogin || this.disposed) return null;
    this.waitingForLogin = true;
    this.setChromeOverlay(false);
    this.dropLegacy();
    this.revision = -1;
    this.snapshot = null;
    this.optimistic = null;
    this.browserPanels.clear();
    for (const key of this.entries.keys()) this.destroyEntry(key);
    return this.showLegacy(path);
  }
  dropLegacy() {
    if (this.legacyTimer) { this.timers.clear(this.legacyTimer); this.timerIds.delete(this.legacyTimer); this.legacyTimer = null; }
    if (this.entries.has(LEGACY_KEY)) this.destroyEntry(LEGACY_KEY);
  }

  /** 打开某个页面（快捷对话设置等）：壳层不写后端导航命令，也不改动对话视图的 URL，
   *  而是请 chrome 发 `page` 命令（`shell:request-page {route}`）。尚无快照（chrome 可能未就绪）时排队，首个快照后发送。 */
  requestPage(route) {
    if (typeof route !== 'string' || !route.startsWith('/')) return;
    if (this.revision >= 0) this.sendChrome('shell:request-page', { route });
    else this.queuedRoutes.push(route);
  }
  flushQueuedRoutes() {
    const routes = this.queuedRoutes;
    this.queuedRoutes = [];
    for (const route of routes) this.sendChrome('shell:request-page', { route });
  }

  // ---- 预热与最近查看 ----
  scheduleWarmup() {
    this.after(this.warmupStep, () => {
      if (this.disposed || !this.snapshot) return;
      const [key] = warmupOrder(this.snapshot, this.realEntries().map((entry) => entry.key), this.recency, this.maxViews);
      const tab = key && this.snapshot.tabs.find((row) => row.key === key);
      if (!tab) return;
      this.createForTab(tab);
      this.scheduleWarmup();
    });
  }
  pruneRecency(tabs) {
    let changed = false;
    for (const key of Object.keys(this.recency)) if (!tabs.has(key)) { delete this.recency[key]; changed = true; }
    if (changed) this.scheduleRecencySave();
  }
  scheduleRecencySave() {
    if (!this.saveRecency || this.recencyTimer) return;
    this.recencyTimer = this.after(RECENCY_SAVE_MS, () => { this.recencyTimer = null; this.flushRecency(); });
  }
  flushRecency() { try { this.saveRecency?.({ ...this.recency }); } catch { /* 记忆失败不影响使用 */ } }

  // ---- 布局与生命周期 ----
  contentSize() { return this.window.getContentBounds(); }
  mainBounds(kind, size = this.contentSize()) {
    return mainContentBounds(size, kind === 'legacy' ? 0 : this.sidebarWidth);
  }
  setSidebarWidth(width) {
    if (!Number.isInteger(width) || width < 0 || width > 600 || this.sidebarWidth === width) return false;
    this.sidebarWidth = width;
    this.layout();
    return true;
  }
  setChromeOverlay(raised) {
    if (this.chromeOverlay === raised || !alive(this.chromeView) || this.window.isDestroyed()) return;
    this.chromeOverlay = raised;
    if (raised) {
      this.chromeOverlayStartKey = this.activeKey;
      this.restoreChromeOverlay();
      return;
    }
    const root = this.window.contentView;
    // Re-adding an existing child always raises it. Detach it before inserting at index 0.
    root.removeChildView(this.chromeView);
    root.addChildView(this.chromeView, 0);
    const content = this.getActiveView();
    if (this.activeKey !== this.chromeOverlayStartKey && this.window.isFocused() && alive(content)) {
      content.webContents.focus();
    }
  }
  restoreChromeOverlay() {
    if (!this.chromeOverlay || !alive(this.chromeView) || this.window.isDestroyed()) return;
    const root = this.window.contentView;
    if (root.children.at(-1) !== this.chromeView) root.addChildView(this.chromeView);
  }
  browserPanelReady(contents) {
    const entry = this.entryForContents(contents);
    if (!entry || !['conv', 'new'].includes(entry.kind)) return;
    if (this.browserPanels.has(entry.key)) contents.send('shell:browser-panel-open', this.browserPanels.get(entry.key));
  }
  reportBrowserPanel(contents, open) {
    const entry = this.entryForContents(contents);
    if (entry && ['conv', 'new'].includes(entry.kind)) this.setBrowserPanel(entry.key, open);
  }
  /** 主侧边栏入口：只切换当前显示标签的内嵌面板（active 防陈旧点击）。 */
  toggleBrowserPanel(key) {
    const entry = this.getActiveEntry();
    if (!entry || entry.key !== key) return false;
    return this.toggleEntryBrowserPanel(entry);
  }
  /** 内容视图自报入口（壳层已按可信 sender 校定其受管视图身份）：只作用于该视图自己的标签，
   *  不受主窗口 active 限制（独立对话窗口里的视图也能切换）。 */
  toggleBrowserPanelFrom(contents) {
    return this.toggleEntryBrowserPanel(this.entryForContents(contents));
  }
  toggleEntryBrowserPanel(entry) {
    if (!entry || !['conv', 'new'].includes(entry.kind) || !alive(entry.view)) return false;
    const open = !this.browserPanels.get(entry.key);
    this.setBrowserPanel(entry.key, open);
    entry.view.webContents.send('shell:browser-panel-open', open);
    return true;
  }
  setBrowserPanel(key, open) {
    if (this.browserPanels.get(key) === open) return;
    this.browserPanels.set(key, open);
    this.sendChrome('shell:browser-panel-state', { key, open });
  }
  layout() {
    if (this.disposed || this.window.isDestroyed()) return;
    const size = this.contentSize();
    this.chromeView?.setBounds(chromeBounds(size, this.sidebarWidth));
    for (const entry of this.entries.values()) {
      if (entry.placement !== 'detached' && alive(entry.view)) entry.view.setBounds(this.mainBounds(entry.kind, size));
    }
    this.restoreChromeOverlay();
    // 独立窗口的视图由各自窗口的 resize 驱动（layoutDetached）。
  }
  after(ms, fn) {
    const id = this.timers.set(() => { this.timerIds.delete(id); fn(); }, ms);
    this.timerIds.add(id);
    return id;
  }
  /** 主窗口 closed：BaseWindow 不拥有视图生命周期，逐个关闭。 */
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.drag = null; // 退出中途拖拽：不 dock、不丢标签（后端仍是 main），窗口随下面的销毁关闭
    for (const id of this.timerIds) this.timers.clear(id);
    this.timerIds.clear();
    this.flushRecency();
    for (const key of [...this.entries.keys()]) this.destroyEntry(key);
    // 主窗口关闭：独立窗口一并销毁（placement 已由后端持久化，下次启动按快照恢复）。
    for (const key of [...this.detachedWindows.keys()]) this.destroyDetachedWindow(key);
    if (alive(this.chromeView)) this.chromeView.webContents.close();
  }
}

const defaultTimers = {
  set: (fn, ms) => { const id = setTimeout(fn, ms); id.unref?.(); return id; },
  clear: (id) => clearTimeout(id)
};
