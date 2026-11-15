// Platform differences stay here; browser, navigation and the renderer remain shared.
export const desktopSupported = (platform = process.platform) => ['darwin', 'win32'].includes(platform);

export function windowChromeOptions(platform = process.platform) {
  return platform === 'win32'
    ? { frame: false, shadow: true }
    : { titleBarStyle: 'hidden', trafficLightPosition: { x: 15, y: 15 } };
}

// height：顶部栏高度。浏览器弹出窗口是 46px（红绿灯 y=15）；独立对话窗口是 38px（红绿灯 y=11）。
// 红绿灯组高约 16px，垂直居中 y = (height - 16) / 2：46 → 15（现状），38 → 11。
export function popoutChromeOptions(platform = process.platform, chromeColor = '', textColor = '', height = 46) {
  return platform === 'win32'
    ? { titleBarStyle: 'hidden', titleBarOverlay: {
      height,
      ...(chromeColor ? { color: chromeColor } : {}),
      ...(textColor ? { symbolColor: textColor } : {})
    } }
    : { titleBarStyle: 'hidden', trafficLightPosition: { x: 15, y: Math.round((height - 16) / 2) } };
}

export function applyPopoutChromeTheme(window, chromeColor, textColor, platform = process.platform, height = 46) {
  if (platform !== 'win32' || !chromeColor || window.isDestroyed()) return;
  window.setTitleBarOverlay(popoutChromeOptions(platform, chromeColor, textColor, height).titleBarOverlay);
}
