// Platform differences stay here; browser, navigation and the renderer remain shared.
export const desktopSupported = (platform = process.platform) => ['darwin', 'win32'].includes(platform);

export function windowChromeOptions(platform = process.platform) {
  return platform === 'win32'
    ? { frame: false, shadow: true }
    : { titleBarStyle: 'hidden', trafficLightPosition: { x: 15, y: 15 } };
}

export function popoutChromeOptions(platform = process.platform, chromeColor = '', textColor = '') {
  return platform === 'win32'
    ? { titleBarStyle: 'hidden', titleBarOverlay: {
      height: 46,
      ...(chromeColor ? { color: chromeColor } : {}),
      ...(textColor ? { symbolColor: textColor } : {})
    } }
    : { titleBarStyle: 'hidden', trafficLightPosition: { x: 15, y: 15 } };
}

export function applyPopoutChromeTheme(window, chromeColor, textColor, platform = process.platform) {
  if (platform !== 'win32' || !chromeColor || window.isDestroyed()) return;
  window.setTitleBarOverlay(popoutChromeOptions(platform, chromeColor, textColor).titleBarOverlay);
}
