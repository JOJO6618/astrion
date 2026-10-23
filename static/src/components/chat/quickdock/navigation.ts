/** Reveal the agent card by scrolling only the dock's own stack. */
export function scrollToSubAgentCard(container: HTMLElement | null): void {
  const card = container?.querySelector<HTMLElement>('[data-quick-dock-section="agents"]');
  if (!container || !card) return;

  const paddingTop = Number.parseFloat(getComputedStyle(container).paddingTop) || 0;
  const top =
    container.scrollTop +
    card.getBoundingClientRect().top -
    container.getBoundingClientRect().top -
    container.clientTop -
    paddingTop;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  container.scrollTo({ top: Math.max(0, top), behavior: reduceMotion ? 'instant' : 'smooth' });
}
