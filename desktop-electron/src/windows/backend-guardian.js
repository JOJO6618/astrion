// The bundled Python guardian joins a kill-on-close job BEFORE it starts the
// backend. Every descendant inherits that job, including terminal processes.
export function backendLaunch({ platform = process.platform, python, args, shellPid = process.pid, helperPath }) {
  if (platform !== 'win32') return { command: python, args };
  if (!Number.isSafeInteger(shellPid) || shellPid <= 0 || !helperPath) {
    throw new Error('invalid_backend_guardian');
  }
  return { command: python, args: ['-u', '-B', helperPath, String(shellPid), '--', python, ...args] };
}
