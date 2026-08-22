// @ts-nocheck
import { runtimeQueueMethods } from './runtimeQueue';
import { systemCommandMethods } from './systemCommand';
import { sendMethods } from './send';
import { chatMethods } from './chat';

export const messageMethods = {
  ...runtimeQueueMethods,
  ...systemCommandMethods,
  ...sendMethods,
  ...chatMethods,
};
