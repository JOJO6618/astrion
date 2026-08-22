import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve, basename } from 'node:path';
import type { Workspace, WorkspaceCatalog } from './types.js';

export const repoRoot = process.env.AGENTS_REPO_ROOT || resolve(new URL('../..', import.meta.url).pathname);
const configPath = resolve(repoRoot, 'config/host_workspaces.json');

function defaultCatalog(): WorkspaceCatalog {
  const fallbackPath = resolve(repoRoot, 'project');
  return {
    default_workspace_id: 'default',
    workspaces: [
      {
        workspace_id: 'default',
        label: '默认工作区',
        path: fallbackPath,
      },
    ],
  };
}

function normalizePath(path: string): string {
  return resolve(path);
}

function slugify(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-._]+|[-._]+$/g, '');
  return slug || 'workspace';
}

export function getHostWorkspacesConfigPath(): string {
  return configPath;
}

export function loadWorkspaceCatalog(): WorkspaceCatalog {
  try {
    const raw = JSON.parse(readFileSync(configPath, 'utf8')) as Partial<WorkspaceCatalog>;
    const workspaces = Array.isArray(raw.workspaces) ? raw.workspaces : [];
    const normalized = workspaces
      .filter((item): item is Workspace => Boolean(item && item.workspace_id && item.path))
      .map((item) => ({
        workspace_id: String(item.workspace_id),
        label: String(item.label || item.workspace_id),
        path: normalizePath(String(item.path)),
      }));
    if (!normalized.length) return defaultCatalog();
    const defaultId = raw.default_workspace_id && normalized.some((ws) => ws.workspace_id === raw.default_workspace_id)
      ? String(raw.default_workspace_id)
      : normalized[0]!.workspace_id;
    return { default_workspace_id: defaultId, workspaces: normalized };
  } catch {
    return defaultCatalog();
  }
}

export function saveWorkspaceCatalog(catalog: WorkspaceCatalog): void {
  mkdirSync(dirname(configPath), { recursive: true });
  writeFileSync(configPath, JSON.stringify(catalog, null, 2), 'utf8');
}

export function findWorkspaceByPath(catalog: WorkspaceCatalog, cwd: string): Workspace | undefined {
  const target = normalizePath(cwd);
  return catalog.workspaces.find((ws) => normalizePath(ws.path) === target);
}

export function createWorkspaceForPath(catalog: WorkspaceCatalog, cwd: string): { catalog: WorkspaceCatalog; workspace: Workspace; created: boolean } {
  const path = normalizePath(cwd);
  const existing = findWorkspaceByPath(catalog, path);
  if (existing) return { catalog, workspace: existing, created: false };

  const baseLabel = basename(path) || 'workspace';
  const baseId = slugify(baseLabel);
  const used = new Set(catalog.workspaces.map((ws) => ws.workspace_id));
  let workspaceId = baseId;
  let suffix = 2;
  while (used.has(workspaceId)) {
    workspaceId = `${baseId}-${suffix}`;
    suffix += 1;
  }

  const workspace: Workspace = {
    workspace_id: workspaceId,
    label: baseLabel,
    path,
  };
  const nextCatalog: WorkspaceCatalog = {
    default_workspace_id: catalog.default_workspace_id || workspaceId,
    workspaces: [...catalog.workspaces, workspace],
  };
  saveWorkspaceCatalog(nextCatalog);
  return { catalog: nextCatalog, workspace, created: true };
}
