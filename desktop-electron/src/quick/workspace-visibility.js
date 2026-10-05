// Full-screen compatibility must not transform the parent app into a UIElement.
// Both the quick panel and screenshot overlays belong to the regular Dock app.
export function configureWorkspaceVisibility(window) {
  window.setVisibleOnAllWorkspaces(true, {
    visibleOnFullScreen: true,
    skipTransformProcessType: true
  });
}
