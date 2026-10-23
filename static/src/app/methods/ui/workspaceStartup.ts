// @ts-nocheck
import { t } from '@/locales';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import { bindWorkspaceRequestHost } from '../workspaceRequestScope';
import { commitWorkspaceIdentity } from './workspaceNavigation';
import { selectWorkspaceIdentity } from './workspaceSelection';

/** Resolve real selection before the route creates scoped sessions or reads drafts. */
export async function prepareInitialWorkspaceIdentity(host, owns) {
  bindWorkspaceRequestHost(host);
  const explicitWorkspace = new URLSearchParams(window.location.search).get('workspace_id') || '';
  if (host.currentHostWorkspaceId && host.composerDraftOwner && !explicitWorkspace)
    return { success: true };
  // An explicit deep link is validated by the server before selecting or loading.
  const query = explicitWorkspace ? `?workspace_id=${encodeURIComponent(explicitWorkspace)}` : '';
  const response = await fetch(`/api/workspace-view${query}`);
  const result = await response.json();
  if (!owns()) return { success: false, superseded: true };
  if (result.code === 'no_workspace') {
    host.workspaceBootstrapActive = true;
    return result;
  }
  if (!response.ok || !result.success || !result.data?.workspace?.workspace_id) {
    throw new Error(result.error || t('appUi.fetchProjectsFailed'));
  }
  const view = result.data;
  host.composerDraftOwner = String(view.draft_owner || '');
  host.versioningHostMode = !!view.host_mode;
  host.dockerProjectMode = !view.host_mode;
  if (explicitWorkspace) {
    const selection = await selectWorkspaceIdentity(host, view.workspace.workspace_id, owns);
    if (!owns() || selection.superseded) return { superseded: true };
  }
  commitWorkspaceIdentity(host, view.workspace.workspace_id, view.workspace);
  host.workspaceBootstrapActive = false;
  if (host.inputMessage) host.scheduleComposerDraftPersist?.('workspace-ready');
  return result;
}
