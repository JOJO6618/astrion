'use strict';

(() => {
  const opacity = {
    clear: 'var(--mask-clear)',
    faint: 'var(--mask-faint)',
    half: 'var(--mask-half)',
    strong: 'var(--mask-strong)',
    solid: 'var(--mask-solid)'
  };
  const gradient = (angle, stops) => `linear-gradient(${angle}deg, ${stops.map(([color, position]) => `${opacity[color]} ${position.toFixed(3)}px`).join(', ')})`;

  function geometry(visibleWidth, height = 26) {
    const width = Math.max(1, visibleWidth);
    const tilt = 11;
    const feather = 10;
    const bandWidth = Math.max(32, Math.min(54, width * 0.14));
    const radians = Math.atan2(height, -tilt);
    const axisX = Math.sin(radians);
    const startX = -bandWidth - tilt - feather * 2;
    const endX = width + feather * 2;
    return { width, height, tilt, feather, bandWidth, angle: radians * 180 / Math.PI, axisX, startX, distance: endX - startX };
  }

  function duration(shape, pixelsPerSecond) {
    if (!Number.isFinite(pixelsPerSecond) || pixelsPerSecond <= 0) {
      throw new RangeError('Sweep speed must be a positive number');
    }
    return shape.distance / pixelsPerSecond * 1000;
  }

  function frame(shape, progress) {
    const x = shape.startX + Math.max(0, Math.min(1, progress)) * shape.distance;
    const back = (x + shape.tilt) * shape.axisX;
    const front = back + shape.bandWidth * shape.axisX;
    const soft = shape.feather * shape.axisX;
    const beam = gradient(shape.angle, [
      ['clear', back - soft], ['faint', back - soft * 0.5],
      ['half', back], ['strong', back + soft * 0.5], ['solid', back + soft],
      ['solid', front - soft], ['strong', front - soft * 0.5],
      ['half', front], ['faint', front + soft * 0.5], ['clear', front + soft]
    ]);
    const reveal = gradient(shape.angle, [
      ['solid', front - soft], ['strong', front - soft * 0.5],
      ['half', front], ['faint', front + soft * 0.5], ['clear', front + soft]
    ]);
    const hide = gradient(shape.angle, [
      ['clear', back - soft], ['faint', back - soft * 0.5],
      ['half', back], ['strong', back + soft * 0.5], ['solid', back + soft]
    ]);
    return { beam, reveal, hide, x, size: `${shape.width}px ${shape.height}px` };
  }

  window.SweepMotion = Object.freeze({ geometry, duration, frame });
})();
