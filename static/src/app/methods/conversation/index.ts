// @ts-nocheck
import { stateMethods } from './state';
import { loadMethods } from './load';
import { actionMethods } from './action';
import { bootstrapMethods } from './bootstrap';

export const conversationMethods = {
  ...stateMethods,
  ...loadMethods,
  ...actionMethods,
  ...bootstrapMethods,
};
