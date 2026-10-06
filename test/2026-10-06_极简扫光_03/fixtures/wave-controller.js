'use strict';

(() => {
  class ThinkingWaveController {
    constructor(surface, motion, { speed = 320, gap = 650 } = {}) {
      this.surface = surface;
      this.motion = motion;
      this.speed = speed;
      this.gap = gap;
      this.reset();
    }

    reset() {
      this.received = '';
      this.committed = '';
      this.active = null;
      this.wave = 0;
      this.nextWaveAt = 0;
      this.finished = false;
      this.lineEnded = false;
      this.surface.commit('');
    }

    receive(chunk, now, finished = false) {
      const previous = this.received;
      if (!this.lineEnded) {
        const combined = this.received + chunk;
        const newline = combined.indexOf('\n');
        this.lineEnded = newline >= 0;
        this.received = this.lineEnded ? combined.slice(0, newline) : combined;
      }
      this.finished = this.finished || finished || this.lineEnded;
      if (this.received === previous || !this.active || this.wave === 1) return;
      const pass = this.active;
      const elapsed = Math.max(0, now - pass.start) / 1000;
      const front = pass.shape.startX + this.speed * elapsed
        + pass.shape.bandWidth + pass.shape.tilt + pass.shape.feather;
      // If the edge already passed this position, leave new text for the next sweep.
      if (this.surface.targetWidth() < front) return;
      pass.target = this.received;
      this.surface.renderTarget(this.committed, pass.target);
      const width = Math.max(1, Math.min(this.surface.targetWidth(), this.surface.lineWidth()));
      // Extend the current route without resetting its edge or physical speed.
      pass.shape.width = width;
      pass.shape.distance = width + pass.shape.feather * 2 - pass.shape.startX;
    }

    update(now) {
      if (this.active) {
        const pass = this.active;
        const duration = this.motion.duration(pass.shape, this.speed);
        const progress = Math.min(1, Math.max(0, (now - pass.start) / duration));
        this.surface.paint(this.motion.frame(pass.shape, progress));
        if (progress < 1) return;
        this.committed = pass.target;
        this.surface.commit(this.committed);
        this.active = null;
        this.nextWaveAt = now + this.gap;
      }
      if (!this.received || now < this.nextWaveAt || this.surface.lineWidth() <= 0) return;
      const target = this.received;
      this.surface.renderTarget(this.committed, target);
      const shape = this.motion.geometry(Math.min(this.surface.targetWidth(), this.surface.lineWidth()));
      this.wave += 1;
      this.active = { target, shape, start: now };
      // Every sweep begins at the left of the whole line, even with no new text.
      this.surface.paint(this.motion.frame(shape, 0));
    }

    getState() {
      const target = this.active ? this.active.target : this.committed;
      return {
        phase: this.active ? 'revealing' : this.wave ? 'waiting' : 'receiving',
        wave: this.wave,
        finished: this.finished,
        received: this.received,
        committed: this.committed,
        target,
        queued: this.received.slice(target.length)
      };
    }
  }

  window.ThinkingWaveController = ThinkingWaveController;
})();
