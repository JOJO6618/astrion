// @ts-nocheck
import { t } from '@/locales';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import { bindWorkspaceRequestHost } from '../workspaceRequestScope';
import { commitWorkspaceIdentity } from './workspaceNavigation';

/** Resolve real selection before the route creates scoped sessions or reads drafts. */
export async function prepareInitialWorkspaceIdentity(host, owns) {
  bindWorkspaceRequestHost(host);
  if (host.currentHostWorkspaceId) return { success: true };
  // With no known workspace, let the server resolve its authenticated selection.
  const response = await fetch('/api/workspace-view');
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
  commitWorkspaceIdentity(host, view.workspace.workspace_id, view.workspace);
  host.versioningHostMode = !!view.host_mode;
  host.dockerProjectMode = !view.host_mode;
  host.workspaceBootstrapActive = false;
  if (host.inputMessage) host.scheduleComposerDraftPersist?.('workspace-ready');
  return result;
}
