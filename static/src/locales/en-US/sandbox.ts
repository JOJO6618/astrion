// Locale namespace: sandbox (en-US)
// Keys must exactly mirror zh-CN/sandbox.ts (enforced by tsc DeepString).
export default {
  // ── Dialog frame ──
  setupTitle: 'Set Up Sandbox Environment',
  setupAriaLabel: 'Sandbox environment setup wizard',

  // ── Intro ──
  introWhat: 'The sandbox is an isolated command execution environment (based on WSL2). All terminal commands run by the AI execute inside the sandbox, isolated from your system.',
  introEffect: 'Without the sandbox, commands that require isolation cannot run and tool calls will fail with errors.',
  introDisk: 'An Alpine mini system (~3MB) will be downloaded and installed to {path} (~100-300MB total with toolchain).',
  introUninstall: 'You can fully uninstall anytime with: wsl --unregister {distro}.',
  introSecure: 'A dedicated sandbox distro is installed (Windows interop disabled); a daily distro like Ubuntu cannot be used instead.',

  // ── Detection states ──
  stateWslMissing: 'WSL2 is not enabled on this system. Setup will first enable WSL2 (a system administrator prompt will appear, and a reboot may be required afterwards).',
  stateDistroMissing: 'WSL2 is ready, but the dedicated sandbox distro is not installed yet. Click install to finish automatically.',
  stateBwrapMissing: 'The sandbox distro exists, but the bubblewrap component is missing. Click install to repair automatically.',
  stateChecking: 'Checking sandbox environment...',

  // ── Phases and steps ──
  phaseEnablingWsl: 'Requesting administrator approval to enable WSL2 (please confirm in the system prompt)...',
  phaseInstallingWsl: 'Downloading and installing WSL2 components; this may take a few minutes...',
  phaseVerifying: 'Verifying installation...',
  phaseDone: 'Setup complete. The sandbox environment is ready.',
  phaseNeedsReboot: 'WSL2 has been enabled, but a reboot is required to continue. Please reboot and reopen this wizard to finish setup.',
  phaseError: 'Setup failed',
  stepCheckWsl: 'Check WSL environment',
  stepCheckDistro: 'Check distro',
  stepDownloadRootfs: 'Download Alpine system',
  stepImportDistro: 'Import WSL2 distro',
  stepWriteConfig: 'Write sandbox configuration',
  stepInstallTools: 'Install sandbox toolchain',
  downloadProgress: 'Downloaded {size}',
  logLabel: 'Setup log',

  // ── Buttons and options ──
  installNow: 'Install Now',
  later: 'Not Now',
  neverAgain: "Don't ask again",
  rebootDone: 'I have rebooted, continue setup',
  uacCancelledHint: 'Administrator approval was cancelled. Enabling WSL2 requires administrator permission; please retry and choose "Yes" in the system prompt.',

  // ── Personalization → General: sandbox section ──
  sectionTitle: 'Sandbox Environment',
  sectionReady: 'Ready',
  sectionMissing: 'Not installed',
  sectionChecking: 'Checking...',
  sectionUnavailable: 'Not applicable in this environment',
  sectionDesc: 'In Windows host mode, commands run isolated inside a WSL2-based sandbox.',
  openWizard: 'Open Setup Wizard',
  recheck: 'Re-check',
  neverAgainSet: '"Don\'t ask again" is on',
  resetNeverAgain: 'Re-enable prompts',
};
