import { placeholderMethods } from './placeholder';
import { probeMethods } from './probe';
import { aiStreamMethods } from './aiStream';
import { toolMethods } from './tool';
import { goalMethods } from './goal';
import { workflowMethods } from './workflow';
import { titleMethods } from './title';
import { lifecycleMethods } from './lifecycle';
import { messagingMethods } from './messaging';
import { syncMethods } from './sync';
import { compressionMethods } from './compression';

export const taskPollingMethods = {
  ...placeholderMethods,
  ...probeMethods,
  ...aiStreamMethods,
  ...toolMethods,
  ...goalMethods,
  ...workflowMethods,
  ...titleMethods,
  ...lifecycleMethods,
  ...messagingMethods,
  ...syncMethods,
  ...compressionMethods,
};
