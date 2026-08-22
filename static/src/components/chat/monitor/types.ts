export type MonitorBubbleVariant = 'info' | 'thinking' | 'error';

export interface MonitorBubbleOptions {
  variant?: MonitorBubbleVariant;
  icon?: string | null;
  iconSvg?: string | null;
  duration?: number;
}

export type MonitorStatusPhase = 'awaiting' | 'playback';

export interface MonitorSceneRuntime {
  waitForResult: (id?: string | number | null) => Promise<any>;
  setStatus: (label: string) => void;
  statusPhase?: MonitorStatusPhase;
}

export interface MonitorDriver {
  resetScene(options?: {
    desktopRoots?: string[];
    preserveBubble?: boolean;
    preservePointer?: boolean;
    preserveWindows?: boolean;
  }): void;
  setDesktopRoots(roots: string[], options?: { immediate?: boolean }): void;
  setManualInteractionEnabled(enabled: boolean): void;
  showSpeechBubble(text: string, options?: MonitorBubbleOptions): void;
  showWaitingBubble(text?: string): void;
  showThinkingBubble(): void;
  hideBubble(): void;
  previewSceneProgress(name: string): void;
  playScene(
    name: string,
    payload: Record<string, any>,
    runtime: MonitorSceneRuntime
  ): Promise<void>;
  destroy(): void;
  preparePendingCreation?(path?: string | null): void;
}
