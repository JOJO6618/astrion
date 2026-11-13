Astrion {version} - Windows x64 Electron migration build

Start here:
  start-validation.cmd
  Opens a separate empty environment under validation-profile.
  Create a workspace and configure a provider in that environment.
  The validation environment does not copy your real provider credentials.

To use existing conversations, providers and workspaces:
  1. Exit the old Astrion desktop application first.
  2. Run start-existing-data.cmd, then press Enter at the informational prompt.
  This uses the existing desktop runtime data directory and its saved setting.
  Running two backends against the same runtime directory is unsupported.

App/astrion-desktop.exe is the actual application with bundled Python.
Keep the entire App folder together. Do not move only the executable.

This migration uses the same Electron shell as macOS. Windows keeps native
modifier double-click handling and WGC window/region capture.

Old WebView2 and Chrome browser data is not migrated in this build.
Old browser data remains untouched. Electron starts with new browser profiles.

Quick Chat enabled: closing the main window leaves the tray running.
Quick Chat disabled: closing the main window exits the app and its backend.

The separate NSIS installer is provided alongside this ZIP. It is intended
for later installation after validation; it can select the existing install
location. The ZIP can be tried without installing anything.

Experimental build. No Windows Authenticode signature and no formal release.
See the delivery report for executed checks and remaining validation limits.
