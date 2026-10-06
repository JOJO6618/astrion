import { onBeforeUnmount, onMounted, watch, type Ref } from 'vue';
import {
  SummarySweepController,
  type SummarySweepInput,
  type SummarySweepSurface
} from '@/components/chat/summarySweepController';
import {
  createSweepMasks,
  createReplacementMasks,
  EMPTY_SUMMARY_MASK
} from '@/components/chat/summarySweepMotion';

export function useSummarySweep(
  root: Ref<HTMLElement | null>,
  content: Ref<HTMLElement | null>,
  base: Ref<HTMLElement | null>,
  incoming: Ref<HTMLElement | null>,
  beam: Ref<HTMLElement | null>,
  outgoing: Ref<HTMLElement | null>,
  outgoingBeam: Ref<HTMLElement | null>,
  getInput: () => SummarySweepInput,
  onSettled: (input: SummarySweepInput) => void,
  onTextChange: () => void
): void {
  let controller: SummarySweepController | null = null;
  let observer: IntersectionObserver | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let media: MediaQueryList | null = null;
  let frameId: number | null = null;
  let lastFrame: number | null = null;
  let clock = 0;
  let visible = true;
  let outgoingWidth = 0;

  const measure = () => {
    let textWidth = beam.value?.getBoundingClientRect().width || 0;
    const track = content.value?.querySelector('.summary-tool-reel-track');
    if (track) {
      const viewport = root.value?.getBoundingClientRect();
      for (const row of Array.from(track.children)) {
        const rect = row.getBoundingClientRect();
        if (viewport && (rect.bottom <= viewport.top || rect.top >= viewport.bottom)) continue;
        const range = document.createRange();
        range.selectNodeContents(row);
        textWidth = Math.max(textWidth, range.getBoundingClientRect().width);
      }
    }
    return {
      width: root.value?.clientWidth || 0,
      textWidth,
      height: root.value?.clientHeight || 26
    };
  };

  const captureFrame = (source: HTMLElement): HTMLElement => {
    const clone = source.cloneNode(true) as HTMLElement;
    const tracks = source.querySelectorAll<HTMLElement>('.summary-tool-reel-track');
    const copies = clone.querySelectorAll<HTMLElement>('.summary-tool-reel-track');
    tracks.forEach((track, index) => {
      // Freeze the actual mid-roll position, rather than the CSS destination.
      copies[index].style.transition = 'none';
      copies[index].style.transform = window.getComputedStyle(track).transform;
    });
    return clone;
  };

  const surface: SummarySweepSurface = {
    setText(text) {
      onTextChange();
      if (base.value) base.value.textContent = text;
      if (incoming.value) {
        incoming.value.textContent = '';
        incoming.value.style.maskImage = EMPTY_SUMMARY_MASK;
      }
      if (beam.value) beam.value.textContent = text;
    },
    renderTarget(prefix, target) {
      if (!base.value || !incoming.value || !beam.value) return;
      base.value.textContent = prefix;
      incoming.value.textContent = target.slice(prefix.length);
      beam.value.textContent = target;
      const prefixWidth = base.value.getBoundingClientRect().width;
      incoming.value.style.left = `${prefixWidth}px`;
      incoming.value.style.maskPosition = `${-prefixWidth}px 0`;
    },
    beginReplacement(text) {
      outgoingWidth = Math.max(outgoingWidth, measure().textWidth);
      if (content.value && outgoing.value && outgoingBeam.value) {
        // If another label arrives mid-replacement, retain the composed frame.
        // Running highlights are omitted; only the two new edges move.
        const frames = [];
        if (outgoing.value.childElementCount) frames.push(captureFrame(outgoing.value));
        frames.push(captureFrame(content.value));
        outgoing.value.replaceChildren(...frames);
        outgoing.value.style.maskImage = 'none';
        outgoingBeam.value.replaceChildren(...frames.map((frame) => frame.cloneNode(true)));
        outgoingBeam.value.querySelectorAll<HTMLElement>('.summary-tool-reel-item').forEach((row) => {
          row.style.color = 'var(--summary-sweep)';
        });
      }
      surface.setText(text);
      return outgoingWidth;
    },
    measure,
    paint(mode, progress, shape) {
      if (!content.value || !incoming.value || !beam.value) return;
      const masks = createSweepMasks(shape, progress);
      content.value.style.maskSize = masks.size;
      incoming.value.style.maskSize = masks.size;
      beam.value.style.maskSize = masks.size;
      content.value.style.maskImage = mode === 'reveal' && !incoming.value.textContent
        ? masks.reveal : 'none';
      incoming.value.style.maskImage = masks.reveal;
      beam.value.style.maskImage = masks.beam;
    },
    paintReplacement(progress, shape) {
      if (!content.value || !beam.value || !outgoing.value || !outgoingBeam.value) return;
      const masks = createReplacementMasks(shape, progress);
      content.value.style.maskSize = masks.incoming.size;
      content.value.style.maskImage = masks.incoming.reveal;
      beam.value.style.maskSize = masks.incoming.size;
      beam.value.style.maskImage = masks.incoming.beam;
      outgoing.value.style.maskSize = masks.outgoing.size;
      outgoing.value.style.maskImage = masks.outgoing.hide;
      outgoingBeam.value.style.maskSize = masks.outgoing.size;
      outgoingBeam.value.style.maskImage = masks.outgoing.beam;
    },
    clear() {
      if (content.value) content.value.style.maskImage = 'none';
      if (beam.value) beam.value.style.maskImage = EMPTY_SUMMARY_MASK;
      outgoing.value?.replaceChildren();
      outgoingBeam.value?.replaceChildren();
      outgoingWidth = 0;
    }
  };

  function stopFrame(): void {
    if (frameId !== null) window.cancelAnimationFrame(frameId);
    frameId = null;
    lastFrame = null;
  }

  function scheduleFrame(): void {
    if (frameId !== null || !visible || document.hidden || !controller?.needsFrame) return;
    frameId = window.requestAnimationFrame((timestamp) => {
      frameId = null;
      if (lastFrame !== null) clock += Math.min(64, timestamp - lastFrame);
      lastFrame = timestamp;
      controller?.tick(clock);
      if (controller?.needsFrame) scheduleFrame();
      else lastFrame = null;
    });
  }

  function update(): void {
    const input = getInput();
    controller?.setInput(input, clock);
    scheduleFrame();
  }

  function onVisibilityChange(): void {
    if (document.hidden) stopFrame();
    else scheduleFrame();
  }

  function onMotionChange(): void {
    controller?.setReducedMotion(!!media?.matches, clock);
    if (controller?.needsFrame) scheduleFrame();
    else stopFrame();
  }

  watch(
    () => {
      const input = getInput();
      return [input.text, input.identity, input.kind, input.animate, input.sweeping,
        input.ready, input.parallel, input.forceComplete];
    },
    update,
    { flush: 'post' }
  );

  onMounted(() => {
    controller = new SummarySweepController(surface, onSettled);
    media = window.matchMedia('(prefers-reduced-motion: reduce)');
    controller.setReducedMotion(media.matches, clock);
    media.addEventListener('change', onMotionChange);
    document.addEventListener('visibilitychange', onVisibilityChange);
    if (root.value) {
      observer = new IntersectionObserver(([entry]) => {
        visible = entry.isIntersecting;
        if (visible) scheduleFrame();
        else stopFrame();
      });
      observer.observe(root.value);
      resizeObserver = new ResizeObserver(() => {
        controller?.refreshLayout();
        scheduleFrame();
      });
      resizeObserver.observe(root.value);
    }
    update();
  });

  onBeforeUnmount(() => {
    stopFrame();
    observer?.disconnect();
    resizeObserver?.disconnect();
    media?.removeEventListener('change', onMotionChange);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    controller?.dispose();
    controller = null;
  });
}
