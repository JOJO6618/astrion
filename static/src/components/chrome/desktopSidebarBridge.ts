interface DesktopSidebarBridge {
  persistentSidebar?: boolean;
  setSidebarWidth?: (width: number) => void;
  setChromeOverlay?: (raised: boolean) => void;
  toggleBrowserPanel?: (key: string) => void;
  browserPanels?: () => Promise<Record<string, boolean>>;
  onBrowserPanelState?: (listener: (state: { key: string; open: boolean }) => void) => () => void;
  browserPanelReady?: () => void;
  reportBrowserPanel?: (open: boolean) => void;
  onBrowserPanelOpen?: (listener: (open: boolean) => void) => () => void;
  focusTab?: (key: string) => void;
}

export function desktopSidebarBridge(): DesktopSidebarBridge | undefined {
  return (window as Window & { astrionShell?: DesktopSidebarBridge }).astrionShell;
}

export function hasDesktopSidebar(): boolean {
  return (
    typeof window !== 'undefined' &&
    desktopSidebarBridge()?.persistentSidebar === true &&
    new URLSearchParams(window.location.search).has('tab')
  );
}

export function hasDesktopBrowserPanel(): boolean {
  const platform = (window as Window & { __ASTRION_PLATFORM__?: string }).__ASTRION_PLATFORM__;
  return platform === 'darwin' || platform === 'win32';
}

export function useDesktopBrowserPanelBridge(setOpen: (open: boolean) => void) {
  const bridge = desktopSidebarBridge();
  const subscribe = bridge?.onBrowserPanelOpen;
  if (!subscribe || !hasDesktopSidebar()) return;
  const unsubscribe = subscribe(setOpen);
  bridge.browserPanelReady?.();
  return unsubscribe;
}
