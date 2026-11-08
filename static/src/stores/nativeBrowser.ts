import { defineStore } from 'pinia';
import {
  browserBridge,
  type BrowserScope,
  type BrowserSnapshot,
  type BrowserRect,
  type BrowserSurfaceMetadata
} from '@/components/browser/types';
import { isMacDesktopShell } from '@/utils/desktopPlatform';
import { browserContextMenuLabels } from '@/components/browser/contextMenuLabels';

export type BrowserSection =
  | ''
  | 'history'
  | 'downloads'
  | 'settings'
  | 'passwords'
  | 'import'
  | 'clear';
export const nativeBrowserAvailable = () => isMacDesktopShell() && !!browserBridge();
let connectEpoch = 0;
let layoutSequence = 0;
let navigationSequence = 0;
export const useNativeBrowserStore = defineStore('nativeBrowser', {
  state: () => ({
    open: false,
    connecting: false,
    navigationPending: false,
    error: '',
    snapshot: null as BrowserSnapshot | null,
    menu: false,
    findOpen: false,
    findText: '',
    device: false,
    deviceWidth: 390,
    deviceHeight: 844,
    resizing: false,
    width: Math.max(
      380,
      Math.min(900, Number(localStorage.getItem('astrion_browser_width')) || 600)
    )
  }),
  getters: {
    active: (state) => state.snapshot?.tabs.find((tab) => tab.id === state.snapshot?.activeId),
    tabs: (state) => state.snapshot?.tabs || [],
    section: (state): BrowserSection =>
      state.snapshot?.tabs.find((tab) => tab.id === state.snapshot?.activeId)?.section || ''
  },
  actions: {
    toggle() {
      this.open = !this.open;
    },
    hide() {
      this.open = false;
      this.menu = false;
    },
    apply(snapshot: BrowserSnapshot) {
      if (
        !this.snapshot ||
        snapshot.lease !== this.snapshot.lease ||
        snapshot.revision < this.snapshot.revision
      )
        return;
      this.snapshot = snapshot;
    },
    suspend() {
      if (this.snapshot) this.geometry(null, false);
      connectEpoch++;
      navigationSequence++;
      this.snapshot = null;
      this.connecting = false;
      this.navigationPending = false;
      this.error = '';
      this.menu = false;
      this.findOpen = false;
    },
    async connect(scope: BrowserScope) {
      this.suspend();
      const epoch = ++connectEpoch;
      this.connecting = true;
      this.error = '';
      try {
        const snapshot = await browserBridge()!.connect(scope, browserContextMenuLabels());
        if (epoch !== connectEpoch) return;
        this.snapshot = snapshot;
        layoutSequence = 0;
      } catch (error) {
        if (epoch === connectEpoch) this.error = String((error as Error).message || error);
      } finally {
        if (epoch === connectEpoch) this.connecting = false;
      }
    },
    async command(action: string, value?: unknown, id?: string) {
      const lease = this.snapshot?.lease;
      if (!lease) return;
      this.error = '';
      try {
        const snapshot = await browserBridge()!.command({ lease, action, value, id });
        if (this.snapshot?.lease !== lease) return;
        this.apply(snapshot);
      } catch (error) {
        if (this.snapshot?.lease === lease) this.error = String((error as Error).message || error);
      }
    },
    geometry(
      rect: BrowserRect | null,
      visible: boolean,
      elevated = false,
      capturesInput = true,
      surface: BrowserSurfaceMetadata = {}
    ) {
      if (!this.snapshot) return;
      browserBridge()?.layout({
        lease: this.snapshot.lease,
        sequence: ++layoutSequence,
        rect,
        visible,
        elevated,
        capturesInput,
        ...surface
      });
    },
    async navigate(value: string, newTab = false) {
      if (!value.trim()) return;
      const sequence = ++navigationSequence;
      this.menu = false;
      this.navigationPending = true;
      try {
        await this.command(newTab ? 'new' : 'navigate', value);
      } finally {
        if (sequence === navigationSequence) this.navigationPending = false;
      }
    },
    showSection(section: BrowserSection) {
      this.menu = false;
      if (section) void this.command('internal', section);
      else if (this.active?.section) void this.command('close');
    },
    setWidth(width: number) {
      this.width = Math.round(Math.max(380, Math.min(innerWidth - 360, width)));
      localStorage.setItem('astrion_browser_width', String(this.width));
    }
  }
});
