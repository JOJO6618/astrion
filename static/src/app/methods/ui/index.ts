// @ts-nocheck
import { gitMethods } from './git';
import { scrollMethods } from './scroll';
import { mobileMethods } from './mobile';
import { composerMethods } from './composer';
import { panelMethods } from './panel';
import { dialogMethods } from './dialog';
import { workspaceTaskMethods } from './workspaceTask';
import { hostWorkspaceMethods } from './hostWorkspace';
import { workspaceMethods } from './workspace';
import { tutorialMethods } from './tutorial';
import { modeMethods } from './mode';
import { modelMethods } from './model';
import { permissionMethods } from './permission';
import { workModeMethods } from './workMode';
import { menuMethods } from './menu';
import { terminalMethods } from './terminal';
import { reviewMethods } from './review';
import { socketMethods } from './socket';
import { routeMethods } from './route';
import { systemMethods } from './system';
import { resizeMethods } from './resize';

export const uiMethods = {
  ...gitMethods,
  ...scrollMethods,
  ...mobileMethods,
  ...composerMethods,
  ...panelMethods,
  ...dialogMethods,
  ...workspaceTaskMethods,
  ...hostWorkspaceMethods,
  ...workspaceMethods,
  ...tutorialMethods,
  ...modeMethods,
  ...modelMethods,
  ...permissionMethods,
  ...workModeMethods,
  ...menuMethods,
  ...terminalMethods,
  ...reviewMethods,
  ...socketMethods,
  ...routeMethods,
  ...systemMethods,
  ...resizeMethods,
};
