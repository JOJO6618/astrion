import { ref } from 'vue';
import { t } from '@/locales';

export function useSecondaryPass() {
  const verified = ref(false);
  // 是否已在服务端配置二级密码；未配置时前端应显示设置引导而非密码输入框
  const configured = ref(true);
  const loading = ref(false);
  const error = ref<string | null>(null);

  const check = async () => {
    loading.value = true;
    error.value = null;
    try {
      const resp = await fetch('/api/admin/secondary/status', { credentials: 'same-origin' });
      if (!resp.ok) throw new Error(t('adminApi.statusRequestFailed', { status: resp.status }));
      const data = await resp.json();
      verified.value = !!data.verified;
      // 兼容旧后端：字段缺失时视为已配置（保持原有输入框行为）
      configured.value = data.configured !== false;
    } catch (err: any) {
      error.value = err.message || t('adminApi.cannotVerifySecondaryPass');
    } finally {
      loading.value = false;
    }
  };

  const verify = async (password: string) => {
    loading.value = true;
    error.value = null;
    try {
      const resp = await fetch('/api/admin/secondary/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ password })
      });
      const data = await resp.json();
      if (!resp.ok || !data.success) {
        throw new Error(data.error || t('adminApi.secondaryPassVerifyFailed'));
      }
      verified.value = true;
    } catch (err: any) {
      error.value = err.message || t('adminApi.verifyFailed');
      verified.value = false;
    } finally {
      loading.value = false;
    }
  };

  return {
    verified,
    configured,
    loading,
    error,
    check,
    verify
  };
}

export type SecondaryPassState = ReturnType<typeof useSecondaryPass>;
