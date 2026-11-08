import type { CredentialRequest, CredentialResponse, CredentialSnapshot } from './credentialTypes';
export interface BrowserScope {
  mode: 'agent' | 'chat';
  workspace: string;
  conversation: string;
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
}
export interface BrowserRect {
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface BrowserSurfaceMetadata {
  surfaceColor?: string;
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
export interface BrowserBridge {
  connect: (scope: BrowserScope, labels?: Record<string, string>) => Promise<BrowserSnapshot>;
  theme: (surfaceColor: string) => void;
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
  onGeometryRequest: (callback: () => void) => () => void;
}
export function browserBridge(): BrowserBridge | undefined {
  return (window as unknown as { astrionBrowser?: BrowserBridge }).astrionBrowser;
}
