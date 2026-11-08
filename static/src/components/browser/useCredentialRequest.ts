import { onBeforeUnmount } from 'vue';
import { t } from '@/locales';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { browserBridge } from './types';
import type { CredentialRequest } from './credentialTypes';

export function useCredentialRequest() {
  const store = useNativeBrowserStore();
  let alive = true;
  onBeforeUnmount(() => {
    alive = false;
  });
  async function request(input: CredentialRequest) {
    const lease = store.snapshot?.lease;
    if (!lease || !alive) throw new Error(t('browser.credentialsChanged'));
    const result = await browserBridge()!.credentials({ ...input, lease });
    if (!alive || store.snapshot?.lease !== lease) throw new Error(t('browser.credentialsChanged'));
    return result;
  }
  return { request, valid: () => alive };
}
export function credentialError(error: unknown) {
  return String((error as Error)?.message || error).replace(
    /^Error invoking remote method '[^']+': (?:Error: )?/,
    ''
  );
}
