import { onBeforeUnmount, onMounted } from 'vue';
import { useUiStore } from '@/stores/ui';
import { t } from '@/locales';
import { scheduleApi, type ScheduleRun } from '@/components/schedules/useSchedules';

export function useScheduleNotifications() {
  const ui = useUiStore();
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  async function poll() {
    try {
      // Electron owns delivery even when all content views are hidden.
      if (
        !(window as unknown as { __ASTRION_DESKTOP__?: boolean }).__ASTRION_DESKTOP__ &&
        document.visibilityState === 'visible'
      ) {
        const runs = await scheduleApi<
          (ScheduleRun & { name: string; token: string; workspace_id: string })[]
        >('/notifications/claim', {});
        if (stopped) return;
        for (const run of runs) {
          const title = `${run.name} · ${t(`schedules.${run.state}`)}`;
          ui.pushToast({
            title: t('schedules.source'),
            message: title,
            type: run.state === 'failed' ? 'error' : 'info',
            duration: 10000
          });
          if ('Notification' in window && Notification.permission === 'granted') {
            try {
              new Notification(title, {
                body: run.summary?.slice(0, 180) || run.reason || '',
                tag: run.id
              });
            } catch {
              /* The visible in-app toast still delivered. */
            }
          }
          await scheduleApi(`/notifications/${run.id}/ack`, { token: run.token });
        }
      }
    } catch {
      /* Session startup/offline: retain the outbox for the next poll. */
    }
    if (!stopped) timer = setTimeout(poll, 5000);
  }
  onMounted(() => {
    void poll();
  });
  onBeforeUnmount(() => {
    stopped = true;
    clearTimeout(timer);
  });
}
