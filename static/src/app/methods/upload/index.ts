// @ts-nocheck
import { pickerMethods } from './picker';
import { processMethods } from './process';
import { confirmMethods } from './confirm';
import { quickMethods } from './quick';
import { dragMethods } from './drag';
import { pasteMethods } from './paste';

export const uploadMethods = {
  ...pickerMethods,
  ...processMethods,
  ...confirmMethods,
  ...quickMethods,
  ...dragMethods,
  ...pasteMethods
};
