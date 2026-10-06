import {
  createSweepGeometry,
  getSweepDuration,
  SUMMARY_SWEEP_SPEED,
  SUMMARY_SWEEP_GAP_MS,
  SUMMARY_REPLACE_GAP_PX,
  type SweepGeometry
} from './summarySweepMotion';

export interface SummarySweepInput {
  text: string;
  identity: string;
  kind: 'thinking' | 'tool' | 'static';
  animate: boolean;
  sweeping: boolean;
  /** False while a tool's intent is still being received. */
  ready?: boolean;
  /** Parallel reels replace the single-tool running sweep. */
  parallel?: boolean;
  /** The model has moved on: expose buffered text without waiting for a pass. */
  forceComplete?: boolean;
}

export interface SummarySweepSurface {
  setText(text: string): void;
  renderTarget(prefix: string, target: string): void;
  /** Capture the visible outgoing frame, then install the incoming text. */
  beginReplacement(text: string): number;
  measure(): { width: number; textWidth: number; height: number };
  paint(mode: 'sweep' | 'reveal', progress: number, shape: SweepGeometry): void;
  paintReplacement(progress: number, shape: SweepGeometry): void;
  clear(): void;
}

interface Pass {
  mode: 'sweep' | 'reveal' | 'replace';
  start: number;
  duration: number;
  shape: SweepGeometry;
  target: string;
}

function sameLabel(a: SummarySweepInput, b: SummarySweepInput): boolean {
  return a.identity === b.identity && a.kind === b.kind &&
    (a.kind === 'thinking' || a.text === b.text);
}

/** Initial reveals, thinking waves and simultaneous replacements share one clock. */
export class SummarySweepController {
  private current: SummarySweepInput | null = null;
  private desired: SummarySweepInput | null = null;
  private committed = '';
  private pass: Pass | null = null;
  private nextSweepAt = 0;
  private reducedMotion = false;
  private finishingPass = false;
  private afterThinkingEntry: SummarySweepInput | null = null;

  constructor(
    private readonly surface: SummarySweepSurface,
    private readonly onSettled: (input: SummarySweepInput) => void
  ) {}

  setInput(input: SummarySweepInput, now: number): void {
    const previousDesired = this.desired;
    if (!input.animate && !this.reducedMotion && previousDesired?.animate &&
        previousDesired.kind === 'thinking' && previousDesired.text &&
        (!this.current || !sameLabel(this.current, previousDesired) || this.hasUnrevealed())) {
      // A short reply may finish before thinking has entered. Complete that
      // entry first, then animate the replacement with the final summary.
      this.afterThinkingEntry = { ...input };
      input = { ...previousDesired, sweeping: false, forceComplete: false };
    } else {
      this.afterThinkingEntry = null;
    }
    this.desired = { ...input };
    if (input.ready === false) {
      if (this.current && this.hasUnrevealed()) this.snap(this.current, now);
      return;
    }
    if (this.reducedMotion) {
      this.snap(input, now);
      return;
    }
    if (this.finishingPass) {
      if (!input.animate || input.forceComplete) {
        if (input.kind === 'thinking' && input.identity === this.current?.identity &&
            input.text !== this.current.text) this.finishCurrentPass(now);
        return;
      }
      this.finishingPass = false;
    }
    if (!this.current || !this.current.text) {
      this.adopt(input, now);
      if (input.animate && !input.forceComplete) this.reveal(now);
      else this.snap(input, now);
      return;
    }
    if (sameLabel(this.current, input)) {
      if (input.kind === 'thinking' && input.animate && !input.forceComplete) {
        this.receiveThinking(input, now);
      } else {
        this.current = { ...input };
      }
      // The incoming row must finish before a parallel reel can take over.
      if (this.pass?.mode === 'replace' || this.pass?.mode === 'reveal') return;
      if (!this.eligible() && this.pass?.mode === 'sweep') {
        if (input.parallel) {
          this.pass = null;
          this.surface.clear();
        } else {
          this.finishCurrentPass(now);
        }
      }
      return;
    }
    if (input.text === this.current.text) {
      // Identity/status changes alone do not need a second reveal.
      this.current = { ...input };
      if (!this.pass) this.snap(input, now);
      return;
    }
    if (!input.text && input.animate) return;
    if ((!input.animate || input.forceComplete) && this.pass &&
        this.pass.mode !== 'replace') {
      // Body completion leaves the current light moving to the right edge.
      // A differing final summary then starts its own simultaneous replacement.
      this.finishCurrentPass(now);
      return;
    }
    if (!this.current.animate && !input.animate && !this.pass) {
      // Historical/static labels do not replay on hydration or locale changes.
      this.snap(input, now);
      return;
    }
    this.replace(input, now);
  }

  setReducedMotion(reduced: boolean, now: number): void {
    this.reducedMotion = reduced;
    if (!this.desired || this.desired.ready === false) return;
    if (reduced) this.snap(this.afterThinkingEntry || this.desired, now);
    else this.setInput(this.desired, now);
  }

  get needsFrame(): boolean {
    if (this.reducedMotion) return false;
    if (this.pass) return true;
    return this.desired?.ready !== false && this.eligible();
  }

  tick(now: number): void {
    if (!this.needsFrame || !this.current || !this.desired) return;
    if (this.pass) {
      const pass = this.pass;
      const progress = Math.min(1, Math.max(0, (now - pass.start) / pass.duration));
      this.paintPass(pass, progress);
      if (progress < 1) return;
      this.pass = null;
      this.finishingPass = false;
      this.committed = pass.target;
      this.surface.setText(this.committed);
      this.surface.clear();
      this.nextSweepAt = now + SUMMARY_SWEEP_GAP_MS;
      if (pass.mode !== 'sweep') this.onSettled({ ...this.current });
      if (this.afterThinkingEntry && this.current.kind === 'thinking') {
        this.desired = this.afterThinkingEntry;
        this.afterThinkingEntry = null;
      }
      if (this.desired.ready === false) return;
      if (!sameLabel(this.current, this.desired)) {
        // Keep the finished pass's visible frame as the outgoing label.
        const next = { ...this.desired };
        if (next.text === this.committed) this.snap(next, now);
        else this.replace(next, now);
        return;
      }
    }
    if (this.desired.ready === false) return;
    if (this.eligible() && now >= this.nextSweepAt) this.startPass('sweep', now);
  }

  refreshLayout(): void {
    if (this.finishingPass || this.pass?.mode === 'replace') return;
    if (this.pass?.mode === 'sweep' && !this.eligible()) {
      this.pass = null;
      this.surface.setText(this.committed);
      this.surface.clear();
    }
  }

  dispose(): void {
    this.pass = null;
    this.afterThinkingEntry = null;
    this.current = null;
    this.desired = null;
    this.surface.clear();
  }

  private hasUnrevealed(): boolean {
    return this.pass?.mode === 'reveal' || this.pass?.mode === 'replace' ||
      (this.current?.kind === 'thinking' && this.current.text !== this.committed);
  }

  private receiveThinking(input: SummarySweepInput, now: number): void {
    this.current = { ...input };
    if (!input.text.startsWith(this.committed)) {
      this.replace(input, now);
      return;
    }
    const pass = this.pass;
    if (!input.sweeping) {
      if (pass) {
        // Include the complete short first line in the existing entry, keeping
        // its position and speed even while the body is already streaming.
        pass.target = input.text;
        if (pass.mode === 'replace') this.surface.setText(pass.target);
        else this.surface.renderTarget(this.committed, pass.target);
        this.extendRoute(pass);
        this.paintPass(pass, Math.min(1, Math.max(0, (now - pass.start) / pass.duration)));
      } else if (input.text !== this.committed) {
        this.startPass(this.committed ? 'sweep' : 'reveal', now);
      }
      return;
    }
    if (!pass || pass.mode !== 'sweep' || !input.text.startsWith(pass.target)) return;
    const front = pass.shape.startX + SUMMARY_SWEEP_SPEED * Math.max(0, now - pass.start) / 1000
      + pass.shape.bandWidth + pass.shape.tilt + pass.shape.feather;
    if (this.surface.measure().textWidth < front) return;
    pass.target = input.text;
    this.surface.renderTarget(this.committed, pass.target);
    this.extendRoute(pass);
    this.paintPass(pass, Math.min(1, Math.max(0, (now - pass.start) / pass.duration)));
  }

  private finishCurrentPass(now: number): void {
    const pass = this.pass;
    if (!pass || !this.current) return;
    this.finishingPass = true;
    if (this.desired?.kind === 'thinking' && this.desired.identity === this.current.identity) {
      this.current.text = this.desired.text;
    }
    if (pass.mode === 'sweep' && this.current.kind === 'thinking') {
      // Already-entered thinking can expose its buffered suffix while the
      // current running light finishes. Initial/replacement entries keep masks.
      this.committed = this.current.text;
      this.surface.setText(this.committed);
      this.surface.clear();
      pass.target = this.committed;
      this.extendRoute(pass);
    }
    this.paintPass(pass, Math.min(1, Math.max(0, (now - pass.start) / pass.duration)));
  }

  private replace(input: SummarySweepInput, now: number): void {
    const outgoingWidth = this.surface.beginReplacement(input.text);
    this.pass = null;
    this.finishingPass = false;
    this.current = { ...input };
    this.committed = '';
    const size = this.surface.measure();
    const shape = createSweepGeometry(
      Math.min(size.width, Math.max(outgoingWidth, size.textWidth)), size.height || 26
    );
    // The incoming front is bandWidth + gap behind the original front, so
    // extend the route until that delayed reveal has completely left the line.
    shape.distance += shape.bandWidth + SUMMARY_REPLACE_GAP_PX;
    this.pass = {
      mode: 'replace', start: now, shape, target: input.text,
      duration: getSweepDuration(shape)
    };
    this.paintPass(this.pass, 0);
  }

  private extendRoute(pass: Pass): void {
    const size = this.surface.measure();
    const width = Math.max(1, Math.min(size.width, size.textWidth));
    if (width <= pass.shape.width) return;
    pass.shape.width = width;
    pass.shape.distance = width + pass.shape.feather * 2 - pass.shape.startX +
      (pass.mode === 'replace' ? pass.shape.bandWidth + SUMMARY_REPLACE_GAP_PX : 0);
    pass.duration = getSweepDuration(pass.shape);
  }

  private paintPass(pass: Pass, progress: number): void {
    if (pass.mode === 'replace') this.surface.paintReplacement(progress, pass.shape);
    else this.surface.paint(pass.mode, progress, pass.shape);
  }

  private snap(input: SummarySweepInput, now: number): void {
    this.adopt(input, now);
    this.committed = input.text;
    this.nextSweepAt = now + SUMMARY_SWEEP_GAP_MS;
    this.onSettled({ ...input });
  }

  private adopt(input: SummarySweepInput, now: number): void {
    this.pass = null;
    this.finishingPass = false;
    this.current = { ...input };
    this.committed = '';
    this.nextSweepAt = now;
    this.surface.setText(input.text);
    this.surface.clear();
  }

  private reveal(now: number): void {
    if (this.current?.text) this.startPass('reveal', now);
    else if (this.current) this.onSettled({ ...this.current });
  }

  private eligible(): boolean {
    return !!this.current?.animate && !this.current.forceComplete &&
      this.current.sweeping && !!this.current.text && this.surface.measure().width > 0;
  }

  private startPass(mode: 'reveal' | 'sweep', now: number): void {
    if (!this.current) return;
    this.current.animate = true;
    this.current.forceComplete = false;
    const target = this.current.text;
    if (this.current.kind === 'thinking') this.surface.renderTarget(this.committed, target);
    const size = this.surface.measure();
    const shape = createSweepGeometry(Math.min(size.width, size.textWidth), size.height || 26);
    this.pass = { mode, start: now, shape, target, duration: getSweepDuration(shape) };
    this.paintPass(this.pass, 0);
  }
}
