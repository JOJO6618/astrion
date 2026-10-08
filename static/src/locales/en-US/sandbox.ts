// Locale namespace: sandbox (en-US)
// Keys must exactly mirror zh-CN/sandbox.ts (enforced by tsc DeepString).
export default {
  // ── Dialog frame ──
  setupTitle: 'Set Up Sandbox Environment',
  setupAriaLabel: 'Sandbox environment setup wizard',

  // ── Intro ──
  introWhat:
    'The sandbox is an isolated command execution environment (based on WSL2). All terminal commands run by the AI execute inside the sandbox, isolated from your system.',
  introEffect:
    'Without the sandbox, commands that require isolation cannot run and tool calls will fail with errors.',
  introDisk:
    'An Alpine mini system (~3MB) will be downloaded and installed to {path} (~100-300MB total with toolchain).',
  introUninstall: 'You can fully uninstall anytime with: wsl --unregister {distro}.',
  introSecure:
    'A dedicated sandbox distro is installed (Windows interop disabled); a daily distro like Ubuntu cannot be used instead.',

  // ── Detection states ──
  stateWslMissing:
    'WSL2 is not enabled on this system. Setup will first enable WSL2 (a system administrator prompt will appear, and a reboot may be required afterwards).',
  stateVmPlatformMissing:
    'WSL is installed, but the Windows "Virtual Machine Platform" feature is not enabled, so WSL2 cannot run. Setup will request administrator approval to enable it, and a reboot is required to finish installation.',
  stateDistroMissing:
    'WSL2 is ready, but the dedicated sandbox distro is not installed yet. Click install to finish automatically.',
  stateBwrapMissing:
    'The sandbox distro exists, but the bubblewrap component is missing. Click install to repair automatically.',
  stateChecking: 'Checking sandbox environment...',

  // ── Phases and steps ──
  phaseEnablingWsl:
    'Requesting administrator approval to enable WSL2 (please confirm in the system prompt)...',
  phaseInstallingWsl: 'Downloading and installing WSL2 components; this may take a few minutes...',
  phaseVerifying: 'Verifying installation...',
  phaseDone: 'Setup complete. The sandbox environment is ready.',
  phaseNeedsReboot:
    'WSL2 has been enabled, but a reboot is required to continue. Please reboot and reopen this wizard to finish setup.',
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
  uacCancelledHint:
    'Administrator approval was cancelled. Enabling WSL2 requires administrator permission; please retry and choose "Yes" in the system prompt.',

  // ── Personalization → General: sandbox section ──
  sectionTitle: 'Sandbox Environment',
  sectionReady: 'Ready',
  sectionMissing: 'Not installed',
  sectionChecking: 'Checking...',
  sectionUnavailable: 'Not applicable in this environment',
  sectionDesc:
    'Windows uses WSL2; Linux isolates commands through an administrator-installed helper.',
  linuxIntro:
    'Install system dependencies and a restricted helper. Astrion and commands still run as an ordinary OS user.',
  linuxNetwork:
    'Restricted networking supports host localhost in both directions and Unix sockets within authorized paths.',
  linuxLocation:
    'The helper is installed at {path}; its configuration and service are maintained by the administrator.',
  linuxMissing:
    'The Linux helper is not ready. Initial support covers Ubuntu 24.04 with systemd, cgroup v2 and AppArmor.',
  linuxTerminal: 'Run this command in a terminal on the Astrion server, then check again.',
  linuxAdmin:
    'Setup requests OS administrator authorization and installs dependencies, the helper and its system service.',
  linuxCommand: 'Administrator install command',
  linuxCheckSystem: 'Check system and permissions',
  linuxDependencies: 'Install system dependencies',
  linuxBuildHelper: 'Compile restricted helper',
  linuxPolicies: 'Install isolation policies',
  linuxService: 'Start helper service',
  linuxVerify: 'Verify ordinary user and isolation',
  openWizard: 'Open Setup Wizard',
  recheck: 'Re-check',
  neverAgainSet: '"Don\'t ask again" is on',
  resetNeverAgain: 'Re-enable prompts'
};
