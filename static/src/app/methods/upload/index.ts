// @ts-nocheck
import { pickerMethods } from './picker';
import { processMethods } from './process';
import { entriesMethods } from './entries';
import { confirmMethods } from './confirm';
import { quickMethods } from './quick';
import { dragMethods } from './drag';
import { pasteMethods } from './paste';

export const uploadMethods = {
  ...pickerMethods,
  ...processMethods,
  ...entriesMethods,
  ...confirmMethods,
  ...quickMethods,
  ...dragMethods,
  ...pasteMethods,
};
