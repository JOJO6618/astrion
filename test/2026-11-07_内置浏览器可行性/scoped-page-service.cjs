// Diagnostic facade, not a production security boundary or ownership system.
// Keep Playwright and all raw CDP/Electron handles inside the trusted adapter.
class ScopedPageService {
  #pages = new Map();
  constructor(entries) {
    for (const [id, page] of entries) this.#pages.set(id, page);
  }
  list() { return [...this.#pages.keys()]; }
  async execute({ tabId, action, selector, value }) {
    const page = this.#pages.get(tabId);
    if (!page) throw new Error('unknown_or_unowned_tab');
    switch (action) {
      case 'fill': return page.locator(selector).fill(value);
      case 'click': return page.locator(selector).click();
      case 'text': return page.locator(selector).textContent();
      default: throw new Error('unsupported_action');
    }
  }
}
module.exports = { ScopedPageService };
