export function cropRectangle(rect, display, image) {
  if (!rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]))) return null;
  if (rect.width <= 0 || rect.height <= 0 || display.width <= 0 || display.height <= 0) return null;
  const sx = image.width / display.width, sy = image.height / display.height;
  const x = Math.max(0, Math.min(image.width, Math.floor(rect.x * sx)));
  const y = Math.max(0, Math.min(image.height, Math.floor(rect.y * sy)));
  const right = Math.min(image.width, Math.ceil((rect.x + rect.width) * sx));
  const bottom = Math.min(image.height, Math.ceil((rect.y + rect.height) * sy));
  const width = right - x, height = bottom - y;
  return width >= 2 && height >= 2 ? { x, y, width, height } : null;
}

// workArea is supplied by macOS and already excludes a visible Dock/menu bar.
// Keep a single bottom anchor regardless of reply, attachment or menu height.
export function quickWindowBounds(workArea, requestedHeight, requestedWidth = 620) {
  const inset = 12;
  const height = Math.round(Math.min(workArea.height - inset * 2, Math.max(130, requestedHeight)));
  const width = Math.round(Math.min(workArea.width - inset * 2, requestedWidth));
  return {
    x: Math.round(workArea.x + (workArea.width - width) / 2),
    y: Math.round(workArea.y + workArea.height - inset - height),
    width, height
  };
}

// The native surface never resizes for content. Only its HTML children change
// height; reserving the whole work area prevents AppKit reallocation/flicker.
export function quickCanvasBounds(workArea) {
  return quickWindowBounds(workArea, workArea.height, 960);
}
