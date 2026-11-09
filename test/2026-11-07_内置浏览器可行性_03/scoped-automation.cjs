// Experimental facade. Raw Electron/CDP remain in the trusted runner, never in renderer IPC.
// This verifies selected ownership checks; it is not a complete production authorization system.
class ScopedAutomation {
  constructor({ describe, resolvePage, expectedOwner }) { Object.assign(this, { describe, resolvePage, expectedOwner }); }
  async check(lease) {
    const state = await this.describe();
    if (['workspace', 'conversation', 'actor'].some(key => state.owner[key] !== this.expectedOwner[key])) throw Error('inactive_owner');
    if (!lease || lease.generation !== state.generation) throw Error('stale_generation');
    const tab = state.tabs.find(tab => tab.id === lease.tabId);
    if (!tab) throw Error('unknown_or_unowned_tab');
    return tab;
  }
  async lease(tabId) {
    const state = await this.describe();
    const lease = { tabId, generation: state.generation };
    await this.check(lease); return lease;
  }
  async execute({ lease, action, selector, value, url }) {
    if (!['fill', 'click', 'text', 'navigate', 'snapshot'].includes(action)) throw Error('unsupported_action');
    if (['fill', 'click', 'text'].includes(action) && (typeof selector !== 'string' || selector.length > 3000)) throw Error('invalid_selector');
    if (action === 'fill' && (typeof value !== 'string' || value.length > 100000)) throw Error('invalid_value');
    if (action === 'navigate') {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw Error('unsupported_navigation');
    }
    const tab = await this.check(lease);
    const page = await this.resolvePage(tab.id);
    await this.check(lease);
    let result;
    switch (action) {
      case 'fill': result = await page.locator(selector).fill(value, { timeout: 8000 }); break;
      case 'click': result = await page.locator(selector).click({ timeout: 8000 }); break;
      case 'text': result = await page.locator(selector).textContent({ timeout: 8000 }); break;
      case 'navigate': await page.goto(url, { timeout: 12000, waitUntil: 'domcontentloaded' }); result = page.url(); break;
      case 'snapshot': result = { url: page.url(), title: await page.title(), text: (await page.locator('body').innerText()).slice(0, 15000) }; break;
    }
    // A switch can happen during an awaited operation. Reject the stale result;
    // this does not claim to undo an action already delivered to Chromium.
    await this.check(lease);
    return result ?? { ok: true };
  }
}
module.exports = { ScopedAutomation };
