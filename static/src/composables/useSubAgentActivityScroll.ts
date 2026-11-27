import { nextTick, watch, type Ref } from 'vue';
import { useSubAgentStore } from '@/stores/subAgent';

export function useSubAgentActivityScroll(
  body: Ref<HTMLElement | null>,
  taskId: () => string | null
) {
  const store = useSubAgentStore();
  let waitingForLive = false;
  let loadingOlder: string | null = null;
  let touchY = 0;

  function loadOlder() {
    const id = taskId();
    if (
      !id ||
      loadingOlder === id ||
      !body.value ||
      body.value.scrollTop > 1 ||
      !store.activityStart
    )
      return;
    store.readingHistory = true;
    if (store.activityLoading) {
      waitingForLive = true;
      return;
    }
    waitingForLive = false;
    loadingOlder = id;
    void store.fetchSubAgentActivity(id, 'older').finally(() => {
      if (loadingOlder === id) loadingOlder = null;
    });
  }

  function onScroll() {
    const el = body.value;
    if (!el || !taskId()) return;
    store.readingHistory = el.scrollHeight - el.clientHeight - el.scrollTop > 1;
    if (store.readingHistory && el.scrollTop <= 1) loadOlder();
  }

  // A batch can collapse to fewer visible rows than fit in the panel.
  // Wheel and touch still allow the user to go back when no scrollbar exists.
  function onWheel(event: WheelEvent) {
    if (event.deltaY < 0) loadOlder();
  }
  function onTouchStart(event: TouchEvent) {
    touchY = event.touches[0].clientY;
  }
  function onTouchEnd(event: TouchEvent) {
    if (event.changedTouches[0].clientY > touchY + 8) loadOlder();
  }

  watch(taskId, () => {
    waitingForLive = false;
  });
  watch(
    () => store.activityLoading,
    async (loading) => {
      if (!loading && waitingForLive) {
        waitingForLive = false;
        await nextTick();
        loadOlder();
      }
    }
  );
  watch(
    [taskId, () => store.livePages[0], () => store.liveOffset],
    async ([id, first, end], [oldId, previous, oldEnd]) => {
      const el = body.value;
      if (!id || !el || !first) return;
      const prepended = id === oldId && previous && first.start < previous.start && end === oldEnd;
      const oldHeight = el.scrollHeight;
      const oldTop = el.scrollTop;
      await nextTick();
      if (body.value !== el || taskId() !== id) return;
      if (prepended) {
        el.scrollTop = oldTop + el.scrollHeight - oldHeight;
      } else if (!store.readingHistory) {
        el.scrollTop = el.scrollHeight;
      }
    }
  );

  return { onScroll, onWheel, onTouchStart, onTouchEnd };
}
