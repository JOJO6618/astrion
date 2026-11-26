export function rustGuardianLaunch({ command, args, env, guardian, shellPid = process.pid }) {
  if (!guardian || !Number.isSafeInteger(shellPid) || shellPid <= 0) throw new Error('invalid_rust_backend_guardian');
  return {
    command: guardian,
    args: ['guard-backend', String(shellPid), '--', command, ...args],
    env: { ...env, ASTRION_DESKTOP_STOP_STDIN: '1' },
    stdio: ['pipe', 'pipe', 'pipe']
  };
}

export function watchGuardedPid(child) {
  child.stdin.on('error', () => {});
  let pending = '';
  function onData(chunk) {
    pending += chunk.toString();
    const lines = pending.split('\n');
    pending = lines.pop().slice(-128);
    for (const line of lines) {
      const prefix = /^ASTRION_BACKEND_PID=([1-9]\d{0,9})\r?$/.exec(line);
      if (prefix && Number(prefix[1]) <= 0xffffffff) {
        child.backendPid = Number(prefix[1]);
        child.stdout.off('data', onData);
        return;
      }
    }
  }
  child.stdout.on('data', onData);
}
