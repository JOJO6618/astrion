// static/src/utils/openExternal.ts - 用系统默认浏览器打开外部 URL（跨宿主统一入口）
//
// 背景：桌面壳（Tauri WebView 加载 External URL）里 window.open 不可用——
// 静默失败什么都不发生（Codex OAuth 授权页打不开就是这个原因）。
// 「帮助」入口已验证的可用通道 = 桌面壳走 /api/system/open-external
// 由后端调系统浏览器；网页端 / Android WebView 直接 window.open
// （App 侧 shouldOverrideUrlLoading 会把外部链接转交系统浏览器）。
//
// 所有「跳出应用打开网页」的入口统一走这里，不要各自写 window.open。

/**
 * 打开外部 URL。桌面壳走系统浏览器；其它宿主开新标签页。
 * 失败静默（调用方多为 fire-and-forget，无兜底通道时不打扰用户）。
 */
export async function openExternal(url: string): Promise<void> {
  if ((window as any).__ASTRION_DESKTOP__) {
    try {
      await fetch('/api/system/open-external', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });
    } catch {
      /* 桌面壳后端不可达时无兜底通道 */
    }
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

/**
 * 用系统默认程序打开工作区内的真实文件（file:// 语义）。
 *
 * 预览面板的「在浏览器打开」对文件目标用它：打开的是文件本身，
 * 而不是预览服务器的代理地址（随机端口 + 对话 token，复制出去无意义）。
 * http(s) 页面无法直接跳 file://，所以必须由后端调系统 open；
 * 传入的是工作区相对路径，后端校验后自行拼真实路径。
 * 失败返回 false，由调用方决定是否退回代理地址。
 */
export async function openWorkspaceFile(path: string): Promise<boolean> {
  const clean = String(path || '').trim();
  if (!clean) return false;
  try {
    const resp = await fetch('/api/system/open-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: clean })
    });
    const payload = await resp.json().catch(() => ({}));
    return !!(resp.ok && payload && payload.success);
  } catch {
    return false;
  }
}
