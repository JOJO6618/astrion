// Windows Python cannot assume Node's extra pipe becomes CRT fd 3. Reuse the
// existing one-shot stdin protocol; Python replaces stdin with NUL after reading.
export function credentialTransport({ platform = process.platform, bridgePort, browserToken }) {
  const enabled = Boolean(bridgePort && browserToken);
  const windows = platform === 'win32';
  return {
    env: enabled ? { ASTRION_DESKTOP_BROWSER_TOKEN_FD: windows ? '0' : '3' } : {},
    stdio: enabled
      ? (windows ? ['pipe', 'pipe', 'pipe'] : ['ignore', 'pipe', 'pipe', 'pipe'])
      : ['ignore', 'pipe', 'pipe'],
    write(child) {
      if (!enabled) return;
      const pipe = windows ? child.stdin : child.stdio[3];
      if (!pipe) throw new Error('browser_token_pipe_missing');
      pipe.on('error', () => {});
      pipe.end(`${browserToken}\n`);
    }
  };
}
