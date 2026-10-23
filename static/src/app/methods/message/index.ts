// @ts-nocheck
import { guidanceMethods } from './guidance';
import { systemCommandMethods } from './systemCommand';
import { sendMethods } from './send';
import { chatMethods } from './chat';

export const messageMethods = {
  ...guidanceMethods,
  ...systemCommandMethods,
  ...sendMethods,
  ...chatMethods
};
