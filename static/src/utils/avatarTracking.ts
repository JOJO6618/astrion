export interface TrackingPoint {
  x: number;
  y: number;
}

/** Mirror-symmetric target in SVG units, based on real client coordinates. */
export function eyeTrackingOffset(pointer: TrackingPoint, center: TrackingPoint): TrackingPoint {
  const dx = pointer.x - center.x,
    dy = pointer.y - center.y;
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return { x: 0, y: 0 };
  const denominator = Math.max(180, Math.hypot(dx, dy));
  return { x: (dx * 10) / denominator, y: (dy * 10) / denominator };
}
