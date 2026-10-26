import { t } from '@/locales';

type TaskError = Record<string, any>;

export function isToolArgumentError(data: TaskError) {
  return (
    data.error_type === 'parameter_format_error' ||
    /(?:\u5de5\u5177\u53c2\u6570\u89e3\u6790\u5931\u8d25|Failed to parse tool arguments)/.test(
      String(data.message || '')
    )
  );
}

/** Shared Agent/Chat presentation; task ownership and lifecycle stay with the caller. */
export function taskErrorToast(data: TaskError) {
  const error = data.message || t('common.unknownError');
  if (data.retry) {
    const seconds = Number(data.retry_in) || 5;
    const attempt = Number(data.attempt) || 1;
    return {
      title: t('appTasks.retrySoonTitle'),
      message: t('appTasks.retryInSeconds', {
        n: seconds,
        attempt,
        max: Number(data.max_attempts) || attempt,
        error
      }),
      type: 'info',
      duration: Math.max(seconds, 1) * 1000
    };
  }
  if (isToolArgumentError(data)) {
    return { title: t('appTasks.toolCallFailed'), message: error, type: 'warning', duration: 6000 };
  }
  let title = t('appTasks.taskFailedTitle');
  let message = error;
  if (data.error_type === 'api_error') {
    title = t('appTasks.apiErrorTitle');
    message = t('appTasks.apiErrorMessage', { error });
  } else if (data.error_type === 'timeout') {
    title = t('appTasks.timeoutTitle');
    message = t('appTasks.timeoutMessage');
  } else if (data.error_type === 'quota_exceeded') {
    title = t('appTasks.quotaTitle');
    message = t('appTasks.quotaMessage');
  }
  return { title, message, type: 'error', duration: 8000 };
}
