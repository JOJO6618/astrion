import type { CredentialRequest, CredentialResponse, CredentialSnapshot } from './credentialTypes';
export interface BrowserScope {
  mode: 'agent' | 'chat';
  workspace: string;
  conversation: string;
  /** Stable desktop tab key; lets the main process migrate /new tabs to the conversation owner. */
  key?: string;
}
export type BrowserInternalSection =
  | 'history'
  | 'downloads'
  | 'settings'
  | 'passwords'
  | 'import'
  | 'clear';
export interface BrowserTab {
  id: string;
  section?: BrowserInternalSection;
  url: string;
  title: string;
  favicon?: string;
  loading: boolean;
  pending?: boolean;
  error: string;
  canGoBack: boolean;
  canGoForward: boolean;
  zoom: number;
  find: { matches: number; active: number } | null;
}
export interface BrowserHistory {
  id: string;
  favicon?: string;
  url: string;
  title: string;
  at: number;
}
export interface BrowserDownload {
  id: string;
  name: string;
  received: number;
  total: number;
  state: string;
  at: number;
}
export interface BrowserPermission {
  id: string;
  tabId: string;
  permission: string;
  origin: string;
}
export interface BrowserSnapshot {
  lease: string;
  revision: number;
  activeId: string;
  tabs: BrowserTab[];
  history: BrowserHistory[];
  downloads: BrowserDownload[];
  permissions: BrowserPermission[];
  credentials?: CredentialSnapshot;
  /** Empty searchEngine means "not chosen yet"; the panel fills a language-based default. */
  settings?: { searchEngine?: BrowserSearchEngine | ''; followAi?: boolean };
  shortcuts?: BrowserShortcuts;
  /** macOS：快照所属界面（主窗口面板或对话独立窗口）。 */
  window?: 'main' | 'popout';
  /** macOS：主窗口当前对话的浏览器已弹出到独立窗口，主窗口只显示占位。 */
  detached?: boolean;
}
export type BrowserSearchEngine = 'google' | 'bing_cn' | 'bing' | 'baidu';
export interface BrowserShortcut {
  url: string;
  title: string;
}
/** pinned: user-added or pinned tiles, in order. hidden: site keys removed from "most visited". */
export interface BrowserShortcuts {
  pinned: BrowserShortcut[];
  hidden: string[];
}
export interface BrowserRect {
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface BrowserSurfaceMetadata {
  surfaceColor?: string;
  /** 主题文字色：只用于纯文本类文档（背景透明 + 默认黑字）的可读性。 */
  textColor?: string;
  occlusions?: BrowserRect[];
  focusFind?: boolean;
  viewport?: { width: number; height: number; panelWidth: number; surfaceInset: number };
}
export interface BrowserEvent {
  lease?: string;
  key?: string;
  message?: string;
  tabId?: string;
  x?: number;
  y?: number;
  canCopy?: boolean;
  editable?: boolean;
}
export interface BrowserInput {
  type: 'mouseDown' | 'mouseUp' | 'mouseMove' | 'mouseWheel';
  x: number;
  y: number;
  button?: number;
  clickCount?: number;
  deltaX?: number;
  deltaY?: number;
  modifiers: string[];
}
export interface BrowserTheme {
  surfaceColor: string;
  textColor?: string;
}
export interface BrowserBridge {
  surfaceMode?: 'regions';
  credentialFileImport?: boolean;
  /** 主窗口桥：支持把对话浏览器弹出到独立窗口。 */
  popout?: boolean;
  /** 桥能力：本壳支持「跟随智能体」（AI 操作哪个标签，用户视图就跟到哪个）。 */
  followAi?: boolean;
  /** 独立窗口桥：本页就是对话浏览器的独立窗口。 */
  window?: 'popout';
  connect: (scope: BrowserScope, labels?: Record<string, string>) => Promise<BrowserSnapshot>;
  theme: (theme: BrowserTheme) => void;
  credentials: (payload: CredentialRequest & { lease: string }) => Promise<CredentialResponse>;
  onCredentialProgress: (callback: (event: { lease: string; stage: string }) => void) => () => void;
  command: (payload: {
    lease: string;
    action: string;
    id?: string;
    value?: unknown;
  }) => Promise<BrowserSnapshot>;
  layout: (
    payload: {
      lease: string;
      sequence: number;
      visible: boolean;
      elevated?: boolean;
      capturesInput?: boolean;
      rect: BrowserRect | null;
    } & BrowserSurfaceMetadata
  ) => void;
  input: (payload: BrowserInput & { lease: string; tabId: string }) => void;
  onState: (callback: (snapshot: BrowserSnapshot) => void) => () => void;
  onShortcut: (callback: (event: BrowserEvent) => void) => () => void;
  onContextMenu: (callback: (event: BrowserEvent) => void) => () => void;
  onMessage: (callback: (event: BrowserEvent) => void) => () => void;
  onAiActivity: (
    callback: (event: { workspace: string; conversation: string }) => void
  ) => () => void;
  onGeometryRequest: (callback: () => void) => () => void;
  /** 独立窗口桥：窗口全屏状态（全屏时不为红绿灯留白）。 */
  onWindowState?: (callback: (state: { fullScreen: boolean }) => void) => () => void;
}
export function browserBridge(): BrowserBridge | undefined {
  return (window as unknown as { astrionBrowser?: BrowserBridge }).astrionBrowser;
}
/** 当前页是对话浏览器的独立窗口。 */
export const browserPopoutWindow = () => browserBridge()?.window === 'popout';
/** 主窗口面板可把对话浏览器弹出到独立窗口（仅 macOS Electron 壳声明）。 */
export const browserCanPopout = () => browserBridge()?.popout === true;
