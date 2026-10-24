---
name: remotion-upgrade
description: Upgrade Remotion, and related packages
version: 4.0.538
---

# Upgrade Remotion

1. Inspect the project manifests and lockfile to identify the package manager and workspaces. Preserve unrelated changes.
2. Keep package upgrades scoped to the user's actual Remotion project. The CLI's `remotion upgrade` can also update project-local skills; inspect the installed version's behavior and use it only if those changes belong to the request. For an application dependency upgrade, use the explicit package-manager procedure below to avoid overwriting Astrion-adapted skill instructions.

3. Upgrade the application packages explicitly:
   - Get the requested or latest stable Remotion version with `npm view remotion version` and check its migration notes.
   - Find every installed `remotion` and `@remotion/*` dependency across the project and upgrade them all to that exact version. Preserve their dependency sections and the project's workspace or catalog conventions.
   - Read the `@remotion/studio` dependencies for the target Remotion version using `npm view @remotion/studio@<version> dependencies --json`. Align installed auxiliary packages such as `zod`, `mediabunny`, and `@huggingface/transformers` with the versions listed there. Use the `mediabunny` version for installed `@mediabunny/*` packages.
   - Run the project's package manager to update its lockfile.
4. Keep the built-in Astrion skill refresh separate from application dependencies. Do not run a generic `skills update` installer as part of upgrading a video project. If the user requests a built-in skill refresh, retrieve the chosen upstream version, directly adapt the relevant content in `agentskills/`, preserve resources and source records, and validate it. Runtime `.astrion/skills` copies are synchronized by Astrion and are not the maintenance source.

5. Review the manifest and lockfile diff. Ensure all Remotion packages use one version and installed auxiliary packages use their recommended versions. If the CLI is available, run `npx remotion versions` as an additional check.

The [Remotion releases](https://github.com/remotion-dev/remotion/releases) contain the changelog and may be useful for summarizing relevant changes after the upgrade.
