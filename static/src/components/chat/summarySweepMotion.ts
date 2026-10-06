export const SUMMARY_SWEEP_SPEED = 320;
export const SUMMARY_SWEEP_GAP_MS = 650;
export const SUMMARY_REPLACE_GAP_PX = 24;
export const EMPTY_SUMMARY_MASK = 'linear-gradient(transparent, transparent)';

export interface SweepGeometry {
  width: number;
  height: number;
  tilt: number;
  feather: number;
  bandWidth: number;
  angle: number;
  axisX: number;
  startX: number;
  distance: number;
}

export interface SweepMasks {
  beam: string;
  reveal: string;
  hide: string;
  size: string;
}

type Opacity = 'clear' | 'faint' | 'half' | 'strong' | 'solid';
const MASK_OPACITY: Record<Opacity, string> = {
  clear: 'transparent',
  faint: 'color-mix(in srgb, var(--text-primary) 16%, transparent)',
  half: 'color-mix(in srgb, var(--text-primary) 50%, transparent)',
  strong: 'color-mix(in srgb, var(--text-primary) 84%, transparent)',
  solid: 'var(--text-primary)'
};

function gradient(angle: number, stops: [Opacity, number][]): string {
  return `linear-gradient(${angle}deg, ${stops
    .map(([opacity, position]) => `${MASK_OPACITY[opacity]} ${position.toFixed(3)}px`)
    .join(', ')})`;
}

export function createSweepGeometry(visibleWidth: number, height = 26): SweepGeometry {
  const width = Math.max(1, visibleWidth);
  const tilt = 11;
  const feather = 10;
  const bandWidth = Math.max(32, Math.min(54, width * 0.14));
  const radians = Math.atan2(height, -tilt);
  const startX = -bandWidth - tilt - feather * 2;
  const endX = width + feather * 2;
  return {
    width,
    height,
    tilt,
    feather,
    bandWidth,
    angle: (radians * 180) / Math.PI,
    axisX: Math.sin(radians),
    startX,
    distance: endX - startX
  };
}

export function getSweepDuration(shape: SweepGeometry): number {
  return (shape.distance / SUMMARY_SWEEP_SPEED) * 1000;
}

export function createSweepMasks(shape: SweepGeometry, progress: number): SweepMasks {
  const x = shape.startX + Math.max(0, Math.min(1, progress)) * shape.distance;
  const back = (x + shape.tilt) * shape.axisX;
  const front = back + shape.bandWidth * shape.axisX;
  const soft = shape.feather * shape.axisX;
  return {
    beam: gradient(shape.angle, [
      ['clear', back - soft], ['faint', back - soft * 0.5],
      ['half', back], ['strong', back + soft * 0.5], ['solid', back + soft],
      ['solid', front - soft], ['strong', front - soft * 0.5],
      ['half', front], ['faint', front + soft * 0.5], ['clear', front + soft]
    ]),
    reveal: gradient(shape.angle, [
      ['solid', front - soft], ['strong', front - soft * 0.5],
      ['half', front], ['faint', front + soft * 0.5], ['clear', front + soft]
    ]),
    hide: gradient(shape.angle, [
      ['clear', back - soft], ['faint', back - soft * 0.5],
      ['half', back], ['strong', back + soft * 0.5], ['solid', back + soft]
    ]),
    size: `${shape.width}px ${shape.height}px`
  };
}

/** The reveal's front follows the outgoing hide's back by exactly 24px. */
export function createReplacementMasks(shape: SweepGeometry, progress: number) {
  const outgoing = createSweepMasks(shape, progress);
  const incoming = createSweepMasks({
    ...shape,
    startX: shape.startX - shape.bandWidth - SUMMARY_REPLACE_GAP_PX
  }, progress);
  return { outgoing, incoming };
}
