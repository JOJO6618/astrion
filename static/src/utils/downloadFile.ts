// static/src/utils/downloadFile.ts - 工作区文件下载统一入口
//
// 两条宿主通道（不要在各业务组件里各自实现）：
// 1. 桌面壳（Windows Tauri / macOS Electron）：Windows 的 WebView2 没有下载 UI，
//    浏览器式的 fetch→blob→a.download 点了没有任何反应；macOS Electron 靠默认行为
//    会弹保存框，两边体验不一致。故桌面壳统一走后端 /api/system/save-file，
//    由宿主后端弹原生「另存为」对话框（与 mac 行为对齐，两端一致）。
// 2. 浏览器：保持原有 fetch → blob → a.download 行为不变。

import { isDesktopShell } from '@/utils/desktopPlatform';

export interface WorkspaceDownloadOptions {
  /** 工作区相对路径 */
  path: string;
  /** 展示/建议文件名，缺省取路径末段 */
  filename?: string;
  /** 目标类型，当前仅支持单文件 */
  kind?: 'file' | 'folder';
}

export interface WorkspaceDownloadOutcome {
  /** saved/cancelled 仅桌面壳宿主对话框会返回；started=已交给浏览器或原生桥 */
  status: 'saved' | 'cancelled' | 'started' | 'failed';
  /** status=failed 时的错误描述（来自后端或前端异常） */
  error?: string;
}

function baseName(path: string): string {
  const segments = path.split(/[\\/]/).filter(Boolean);
  return segments.length ? segments[segments.length - 1] : 'download';
}

function downloadUrl(path: string, kind: 'file' | 'folder'): string {
  return kind === 'folder'
    ? `/api/download/folder?path=${encodeURIComponent(path)}`
    : `/api/download/file?path=${encodeURIComponent(path)}`;
}

/** 桌面壳：请求宿主后端弹原生保存对话框并把文件写到用户选定位置 */
async function saveViaHostDialog(
  path: string,
  filename: string
): Promise<WorkspaceDownloadOutcome> {
  try {
    const response = await fetch('/api/system/save-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, default_name: filename, kind: 'file' })
    });
    let payload: any = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok || !payload?.success) {
      return { status: 'failed', error: payload?.error || response.statusText };
    }
    return payload.data?.saved ? { status: 'saved' } : { status: 'cancelled' };
  } catch (error) {
    return { status: 'failed', error: (error as Error)?.message || String(error) };
  }
}

/** 浏览器：原有 blob 下载通道（保留错误信息，便于调用方提示） */
async function browserDownload(
  path: string,
  filename: string,
  kind: 'file' | 'folder'
): Promise<WorkspaceDownloadOutcome> {
  return browserDownloadUrl(downloadUrl(path, kind), filename);
}

async function browserDownloadUrl(
  url: string,
  filename: string
): Promise<WorkspaceDownloadOutcome> {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      let message = response.statusText;
      try {
        const errorData = await response.json();
        message = errorData.error || errorData.message || message;
      } catch {
        // 响应体非 JSON 时沿用 statusText
      }
      return { status: 'failed', error: message };
    }
    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    return { status: 'started' };
  } catch (error) {
    return { status: 'failed', error: (error as Error)?.message || String(error) };
  }
}

/**
 * 下载/另存工作区文件。所有下载入口（文件卡片、download:// 链接、快捷 dock、
 * 文件树右键、chatActions）都应调用本函数。
 */
export async function downloadWorkspaceFile(
  options: WorkspaceDownloadOptions
): Promise<WorkspaceDownloadOutcome> {
  const path = (options.path || '').trim();
  if (!path) return { status: 'failed', error: 'missing path' };
  const kind = options.kind || 'file';
  const filename = options.filename || baseName(path);

  // 桌面壳：宿主原生保存对话框（Windows WebView2 无下载 UI，必须走这条）
  if (isDesktopShell()) {
    if (kind === 'folder') {
      // 宿主端点当前只支持单文件（文件夹下载端点已因安全审计下线）
      return { status: 'failed', error: 'folder not supported' };
    }
    const outcome = await saveViaHostDialog(path, filename);
    if (outcome.status !== 'failed') return outcome;
    console.warn('[download] 宿主保存失败，回退浏览器下载:', outcome.error);
  }

  return await browserDownload(path, filename, kind);
}

/** Authenticated application exports use the same desktop / web channels. */
export async function downloadApplicationFile(
  url: string,
  filename: string
): Promise<WorkspaceDownloadOutcome> {
  const target = new URL(url, window.location.href);
  if (target.origin !== window.location.origin)
    return { status: 'failed', error: 'invalid export URL' };
  if (isDesktopShell()) {
    try {
      await (window as any).ensureCsrfToken?.();
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename })
      });
      const result = await response.json();
      if (!response.ok || !result.success)
        return { status: 'failed', error: result.error || response.statusText };
      return { status: result.data?.saved ? 'saved' : 'cancelled' };
    } catch (cause) {
      return { status: 'failed', error: (cause as Error).message };
    }
  }
  return browserDownloadUrl(url, filename);
}
