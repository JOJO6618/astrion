async function runChatRegression(page) {
  page.__chatRegressionRun = runChatRegression;
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  const phase = page.__chatRegressionPhase || 'messages';
  if (!page.__chatRegression) {
    const state = { passed: [], errors: [], failed: [], conversation: '' };
    page.on('pageerror', error => state.errors.push(error.message));
    page.on('response', response => {
      if (response.status() >= 400) state.failed.push({ status: response.status(), url: response.url() });
    });
    page.__chatRegression = state;
  }
  const state = page.__chatRegression;
  const base = 'http://127.0.0.1:8097';
  const editor = () => page.locator('.stadium-input-editor[contenteditable="true"]');
  const waitIdle = async () => {
    await page.waitForFunction(() => !document.querySelector('.send-btn .stop-icon'), null, { timeout: 20000 });
  };
  const open = async () => {
    await page.goto(`${base}/chat/new`);
    await editor().waitFor();
  };
  const send = async text => {
    await editor().fill(text);
    await page.locator('.send-btn:not([disabled])').click();
    await page.waitForSelector('.assistant-message');
  };
  const documentSnapshot = async () => {
    const id = 'conv_' + page.url().split('/').pop();
    const response = await page.request.get(`${base}/api/chat/conversations/${id}`);
    const body = await response.json();
    assert(response.ok(), JSON.stringify(body));
    return body.data.document;
  };
  if (phase === 'messages') {
    await page.setViewportSize({ width: 1200, height: 800 });
    await open();
    assert(await page.locator('.composer-approval-dock').count() === 0, 'Chat mounted approval dock');
    await send('Browser regression plain');
    await page.waitForSelector('.assistant-generating-placeholder');
    assert((await page.locator('.assistant-message .message-header').first().innerText()).includes('Astrion'), 'Reply header absent');
    await page.waitForFunction(() => document.querySelector('.composer-avatar__text')?.textContent.includes('等待 API'), null, { timeout: 10000 });
    await page.screenshot({ path: 'test/2026-10-25_Chat模式/browser-waiting.png' });
    state.passed.push('Send shows user, original Astrion header, generating block and original API-wait status');
    await page.waitForFunction(() => document.querySelector('.composer-avatar__text')?.textContent.includes('思考'), null, { timeout: 10000 });
    state.passed.push('Actual thinking chunks drive original thinking status');
    await page.waitForFunction(() => Array.from(document.querySelectorAll('.text-content')).some(el => el.textContent.includes('Reply received:')), null, { timeout: 10000 });
    assert(await page.locator('.send-btn .stop-icon').count() === 1, 'Reply appeared only after completion');
    const captions = await page.locator('.composer-avatar__text').allTextContents();
    assert(captions.every(text => text === ''), 'Text output invented avatar caption');
    await waitIdle();
    assert((await page.locator('.messages-area').innerText()).includes('Reply received: Browser regression plain'), 'Final reply missing');
    assert(await page.locator('.assistant-generating-placeholder').count() === 0, 'Stale waiting block');
    assert(!(await page.locator('body').innerText()).includes('正在回复'), 'Chat-specific avatar caption remains');
    state.conversation = page.url();
    state.passed.push('Text displays while streaming; final snapshot clears waiting state without custom avatar text');
  } else if (phase === 'branches') {
    assert(state.conversation, 'Run messages phase first');
    await page.goto(state.conversation);
    await editor().waitFor();
    const original = await documentSnapshot();
    await page.locator('.user-message').first().hover();
    await page.locator('.user-message').first().getByRole('button', { name: '编辑', exact: true }).click();
    await page.locator('.chat-message-editor textarea').fill('Saved user edit');
    await page.locator('.chat-message-editor').getByRole('button', { name: '保存', exact: true }).click();
    await page.waitForSelector('.chat-message-editor', { state: 'detached' });
    const edited = await documentSnapshot();
    assert(edited.messages.length === original.messages.length, 'Save created a branch');
    assert((await page.locator('.messages-area').innerText()).includes('Saved user edit'), 'Saved edit missing');
    state.passed.push('User edit/save changes content without creating a branch');
    await page.locator('.assistant-message').last().hover();
    await page.locator('.assistant-message').last().getByRole('button', { name: '重新生成', exact: true }).click();
    await page.waitForSelector('.send-btn .stop-icon');
    await waitIdle();
    const regenerated = await documentSnapshot();
    assert(regenerated.messages.length > edited.messages.length, 'Regeneration did not preserve previous reply');
    assert(await page.locator('.chat-branch-count').last().innerText() === '2/2', 'New version absent');
    await page.locator('.assistant-message').last().hover();
    await page.locator('.assistant-message').last().getByRole('button', { name: '上一个版本', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.chat-branch-count')?.textContent.includes('1/2'), null, { timeout: 10000 });
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.messages-area')?.textContent.includes('Reply received: Saved user edit'), null, { timeout: 10000 });
    assert((await page.locator('.messages-area').innerText()).includes('2/2'), 'Refresh did not restore latest branch');
    state.passed.push('Regeneration creates version 2/2, browsing shows 1/2, reload returns to latest 2/2');
  } else if (phase === 'tools') {
    await open();
    await send('tool preference request');
    await waitIdle();
    const doc = await documentSnapshot();
    assert(doc.messages.some(message => message.role === 'tool'), 'Tool result not persisted');
    assert((await page.locator('.messages-area').innerText()).includes('Reply received: tool preference request'), 'Tool continuation missing');
    assert(await page.locator('.assistant-message').count() === 2, 'Duplicate tool message block');
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.messages-area')?.textContent.includes('Reply received: tool preference request'), null, { timeout: 10000 });
    assert(await page.locator('.assistant-message').count() === 2, 'Reload duplicated tool message block');
    state.passed.push('Real update_memory tool and continuation render exactly two assistant blocks before and after reload');
  } else if (phase === 'stop') {
    await open();
    await send('slow stop request');
    await page.waitForFunction(() => Array.from(document.querySelectorAll('.text-content')).some(el => el.textContent.includes('Reply received:')), null, { timeout: 10000 });
    await page.locator('.send-btn').click();
    await waitIdle();
    const doc = await documentSnapshot();
    const partial = doc.messages.find(message => message.role === 'assistant' && message.metadata?.stopped);
    assert(partial?.content.includes('Reply received:'), 'Stopped body not persisted with stopped status');
    assert((await page.locator('.messages-area').innerText()).includes(partial.content), 'Stopped body not shown');
    assert(await page.locator('.assistant-generating-placeholder').count() === 0, 'Stop left generating block');
    assert((await page.locator('.composer-avatar__text').allTextContents()).every(text => text === ''), 'Stop left avatar caption');
    await page.reload();
    await page.waitForFunction(text => document.querySelector('.messages-area')?.textContent.includes(text), partial.content, { timeout: 10000 });
    assert(await page.locator('.assistant-message').count() === 1, 'Partial duplicated after reload');
    state.passed.push('Stop persists received body, clears wait/avatar state, and reload preserves exactly one partial reply');
  } else if (phase === 'draft') {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    const pattern = '**/api/chat/drafts/new';
    const handler = async route => { await pending; await route.continue(); };
    await page.route(pattern, handler);
    try {
      const read = page.waitForRequest(request => request.url().endsWith('/api/chat/drafts/new') && request.method() === 'GET');
      await page.goto(`${base}/chat/new`);
      await read;
      assert(await page.locator('.stadium-input-editor[contenteditable="false"]').count() === 1, 'Editor unlocked before draft restoration');
      assert(await page.locator('.send-btn:disabled').count() === 1, 'Send enabled before draft restoration');
      release();
      await editor().waitFor();
      await editor().fill('Draft initialized safely');
      assert(await page.locator('.send-btn:not([disabled])').count() === 1, 'Send did not unlock after draft restoration');
      state.passed.push('Delayed draft read keeps editor/send locked; restored editor accepts input without clearing it');
    } finally {
      release();
      await page.unroute(pattern, handler);
    }
  } else if (phase === 'mobile') {
    await page.setViewportSize({ width: 390, height: 844 });
    await open();
    await send('Mobile browser reply');
    await waitIdle();
    assert(await page.locator('.messages-area').getByText('Reply received: Mobile browser reply', { exact: true }).isVisible(), 'Mobile reply invisible');
    assert(await editor().isVisible(), 'Mobile editor invisible');
    assert(await page.locator('.assistant-message .message-header').first().isVisible(), 'Mobile reply header invisible');
    assert(await page.locator('.composer-approval-dock').count() === 0, 'Mobile approvals mounted');
    await page.screenshot({ path: 'test/2026-10-25_Chat模式/browser-mobile.png', fullPage: true });
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.screenshot({ path: 'test/2026-10-25_Chat模式/browser-final.png', fullPage: true });
    state.passed.push('390px mobile shows actual reply, original header and editor without approvals');
  } else throw Error(`Unknown phase: ${phase}`);
  assert(state.errors.length === 0, `Browser exceptions: ${JSON.stringify(state.errors)}`);
  assert(state.failed.length === 0, `HTTP failures: ${JSON.stringify(state.failed)}`);
  return { phase, passed: state.passed, count: state.passed.length, browserErrors: state.errors, failedResponses: state.failed, model: 'isolated deterministic streaming fixture' };
}
