async (page) => {
  const context = await page.context().browser().newContext({ viewport: { width: 1440, height: 1000 } });
  const tab = await context.newPage();
  const results = [];
  const errors = [];
  tab.on('pageerror', (error) => errors.push(error.message));
  tab.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const history = () => tab.evaluate(() => JSON.parse(localStorage.getItem('astrion-chat-branch-prototype-v1')));
  const count = (h) => Object.keys(h.messages).length;
  const idle = () => tab.waitForFunction(() => {
    const h = JSON.parse(localStorage.getItem('astrion-chat-branch-prototype-v1'));
    return h && Object.values(h.messages).every((message) => message.status !== 'streaming');
  });
  const navigate = async (id) => {
    if (await tab.locator('#inspector').isHidden()) await tab.locator('#toggle-inspector').click();
    await tab.getByRole('tab', { name: '消息树', exact: true }).click();
    await tab.locator(`[data-tree-id="${id}"]`).click();
  };
  try {
    await tab.goto(new URL('/', page.url()).href);
    await tab.locator('article').last().waitFor();
    const originalIds = await tab.locator('article').evaluateAll((nodes) => nodes.map((node) => node.dataset.messageId));
    const originalContent = await tab.locator('article .message-content').allTextContents();
    check(originalIds.length === 4, '示例应有4条消息');
    await tab.screenshot({ path: 'test/2026-10-25_Chat分支原型/desktop-classic.png', fullPage: true });

    await tab.locator('article').first().getByRole('button', { name: '编辑', exact: true }).click();
    check(await tab.getByRole('button', { name: '保存', exact: true }).isVisible(), '用户编辑缺少保存按钮');
    check(await tab.locator('.editor-actions').getByRole('button', { name: '发送', exact: true }).isVisible(), '用户编辑缺少发送按钮');
    await tab.getByRole('textbox', { name: '编辑用户消息' }).fill('只保存，不创建分支');
    await tab.getByRole('button', { name: '保存', exact: true }).click();
    let h = await history();
    check(count(h) === 4, '保存用户消息不应创建节点');
    check(h.currentId === originalIds[3], '保存不应改变当前路径末端');
    check(h.messages[originalIds[0]].content === '只保存，不创建分支', '保存没有更新原用户节点');
    check(h.messages[originalIds[0]].childrenIds[0] === originalIds[1], '保存丢失原用户消息的后续');
    check(await tab.locator('article').count() === 4, '保存应保留原后续显示');
    check(await tab.locator('.version-number').count() === 0, '用户保存不应出现分支点');
    results.push('用户编辑保存：原节点更新，节点数/版本数/后续不变，无分支点');

    await tab.locator('article').nth(1).getByRole('button', { name: '编辑', exact: true }).click();
    await tab.getByRole('textbox', { name: '编辑AI回复' }).fill('只修改这条历史AI回复');
    await tab.getByRole('button', { name: '保存', exact: true }).click();
    h = await history();
    check(count(h) === 4 && h.currentId === originalIds[3], 'AI历史编辑不应增加节点或改变末端');
    check(h.messages[originalIds[1]].content === '只修改这条历史AI回复', 'AI保存未原地更新');
    check(h.messages[originalIds[1]].childrenIds[0] === originalIds[2], 'AI编辑丢失原后续');
    check(await tab.locator('article').count() === 4, 'AI保存应保留原后续显示');
    check(await tab.locator('.version-number').count() === 0, 'AI编辑不应出现分支点');
    results.push('历史AI编辑保存：原节点及后续保留，不创建版本或分支点');

    await tab.locator('article').first().getByRole('button', { name: '编辑', exact: true }).click();
    await tab.getByRole('textbox', { name: '编辑用户消息' }).fill('编辑后立即发送');
    await tab.locator('.editor-actions').getByRole('button', { name: '发送', exact: true }).click();
    await idle();
    h = await history();
    check(count(h) === 6 && h.messages[h.currentId].role === 'assistant', '发送应创建用户节点和回复');
    check(await tab.locator('article').count() === 2, '新根版本应有一问一答');
    const generatedId = h.currentId;
    const otherRootId = h.messages[generatedId].parentId;
    const generatedContent = h.messages[generatedId].content;
    await tab.getByRole('tab', { name: '请求上下文', exact: true }).click();
    check(await tab.locator('.context-entry').count() === 1, '新根生成上下文应只有编辑后的用户消息');
    check(await tab.locator('#context').textContent() === 'user编辑后立即发送', '模型上下文混入旧分支');
    results.push('用户编辑发送：创建新分支并生成；请求只含选中路径');

    await tab.locator('article').last().getByRole('button', { name: '编辑', exact: true }).click();
    check(await tab.locator('.editor-actions').getByRole('button', { name: '发送', exact: true }).count() === 0, 'AI编辑不应出现发送按钮');
    await tab.getByRole('textbox', { name: '编辑AI回复' }).fill('手动修改后的AI回复');
    await tab.getByRole('button', { name: '保存', exact: true }).click();
    h = await history();
    const editedAiId = h.currentId;
    check(count(h) === 6 && editedAiId === generatedId, 'AI保存不应创建新节点');
    check(h.messages[generatedId].content === '手动修改后的AI回复' && h.messages[generatedId].content !== generatedContent, 'AI保存应修改原回复');
    check(await tab.locator('article').last().locator('.version-number').count() === 0, 'AI保存不应创建版本切换器');
    await tab.getByRole('textbox', { name: '消息', exact: true }).fill('沿着手动编辑版继续');
    await tab.locator('#send-button').click();
    await idle();
    h = await history();
    check(h.messages[h.messages[h.currentId].parentId].parentId === editedAiId, '后续没有接在原地编辑的AI下');
    results.push('AI末条编辑保存：节点身份不变，可在原路径继续对话');
    const newBranchLeaf = h.currentId;
    await tab.locator('article').first().getByRole('button', { name: '上一个版本' }).click();
    check((await history()).currentId === originalIds[3], '旧分支未恢复其原有后续');
    await tab.locator('article').first().getByRole('button', { name: '下一个版本' }).click();
    check((await history()).currentId === newBranchLeaf, '新分支未恢复后续');
    results.push('仅编辑发送产生分支点，版本切换完整恢复各自后续');

    await navigate(originalIds[1]);
    check((await history()).currentId === originalIds[3], '导航未回到旧后续');
    await tab.locator(`article[data-message-id="${originalIds[1]}"]`).getByRole('button', { name: '重新生成' }).click();
    await tab.waitForFunction(() => Object.values(JSON.parse(localStorage.getItem('astrion-chat-branch-prototype-v1')).messages).some((m) => m.status === 'streaming'));
    h = await history();
    const streamingId = h.currentId;
    await navigate(originalIds[1]);
    await idle();
    h = await history();
    check(h.currentId === originalIds[3], '后台完成抢走了当前分支');
    check(h.messages[streamingId].status === 'complete', '原分支未接收流式结果');
    check(h.messages[originalIds[3]].content === originalContent[3], '流式结果污染旧回复');
    await tab.getByRole('tab', { name: '请求上下文', exact: true }).click();
    check(await tab.locator('.context-entry').count() === 1, '历史AI重新生成错误包含了旧后续');
    results.push('历史回复重新生成；生成中切换分支不串支、不抢回视图');

    await tab.locator('#fail-button').click();
    await tab.locator('article').last().getByRole('button', { name: '重新生成' }).click();
    await idle();
    h = await history();
    const failedId = h.currentId;
    const failedSnapshot = JSON.stringify(h.messages[failedId]);
    check(h.messages[failedId].status === 'error' && h.messages[failedId].content.length > 0, '失败应保留部分回复');
    await tab.locator('article').last().getByRole('button', { name: '重试', exact: true }).click();
    await idle();
    h = await history();
    check(h.messages[h.currentId].status === 'complete', '重试未成功');
    check(h.messages[h.currentId].sourceId === failedId, '重试缺少来源');
    check(JSON.stringify(h.messages[failedId]) === failedSnapshot, '重试覆盖了失败记录');
    const retriedId = h.currentId;
    await tab.locator('article').last().getByRole('button', { name: '上一个版本' }).click();
    check((await history()).currentId === failedId, '不能切回失败版');
    await navigate(originalIds[3]);
    await navigate(otherRootId);
    await navigate(originalIds[0]);
    check((await history()).currentId === originalIds[3], '嵌套分支没有记住上次选择');
    check(retriedId !== failedId, '重试必须创建新节点');
    results.push('故障与重试：新建兄弟版本，保留失败内容；嵌套选择记忆正确');

    await tab.locator('article').last().getByRole('button', { name: '重新生成' }).click();
    await tab.waitForFunction(() => {
      const h = JSON.parse(localStorage.getItem('astrion-chat-branch-prototype-v1'));
      return h.messages[h.currentId]?.status === 'streaming' && h.messages[h.currentId].content.length > 0;
    });
    await tab.locator('#stop-button').click();
    await idle();
    h = await history();
    check(h.messages[h.currentId].status === 'stopped', '停止未落盘');
    check(await tab.locator('article').last().getByRole('button', { name: '重试', exact: true }).isVisible(), '停止后应可重试');
    results.push('停止生成：保留部分内容并提供重试');

    await tab.locator('article').last().getByRole('button', { name: '重试', exact: true }).click();
    await tab.waitForFunction(() => {
      const h = JSON.parse(localStorage.getItem('astrion-chat-branch-prototype-v1'));
      return h.messages[h.currentId]?.status === 'streaming' && h.messages[h.currentId].content.length > 0;
    });
    const interrupted = await history();
    await tab.reload();
    await tab.locator('article').last().getByRole('button', { name: '重试', exact: true }).waitFor();
    h = await history();
    check(h.currentId === interrupted.currentId, '流式刷新后节点身份改变');
    check(h.messages[h.currentId].status === 'stopped', '刷新后的停止状态没有立即持久化');
    check(h.messages[h.currentId].content.startsWith(interrupted.messages[h.currentId].content), '刷新丢失已保存的部分回复');
    check(count(h) === count(interrupted), '刷新意外创建了新节点');
    results.push('流式中刷新：保留原节点和部分回复，停止状态立即持久化');

    const beforeInvalid = count(h);
    await tab.locator('article').first().getByRole('button', { name: '编辑', exact: true }).click();
    await tab.getByRole('textbox', { name: '编辑用户消息' }).fill('   ');
    await tab.getByRole('button', { name: '保存', exact: true }).click();
    check(count(await history()) === beforeInvalid, '空编辑不应创建节点');
    check((await tab.locator('#notice').textContent()).includes('不能为空'), '空编辑未提示错误');
    await tab.getByRole('button', { name: '取消', exact: true }).click();
    results.push('空编辑校验；取消不会创建分支');

    const unsafeText = '<img src=x onerror=alert(1)> 保留为纯文本';
    await tab.getByRole('textbox', { name: '消息', exact: true }).fill(unsafeText);
    await tab.locator('#send-button').click();
    await idle();
    check(await tab.locator('#messages img').count() === 0, '用户文本被当成HTML执行');
    check((await tab.locator('#messages').textContent()).includes(unsafeText), '文本未原样展示');
    const beforeReload = await history();
    await tab.reload();
    await tab.locator('article').last().waitFor();
    check(JSON.stringify(await history()) === JSON.stringify(beforeReload), '刷新改变了完整消息树');
    const downloadPromise = tab.waitForEvent('download');
    await tab.locator('#export-button').click();
    const download = await downloadPromise;
    check(download.suggestedFilename() === 'chat-branch-prototype.json', '导出文件名错误');
    results.push('文本安全展示、刷新恢复完整树、JSON导出');

    await tab.getByRole('button', { name: '深色', exact: true }).click();
    check(await tab.locator('html').getAttribute('data-theme') === 'dark', '深色主题切换失败');
    await tab.screenshot({ path: 'test/2026-10-25_Chat分支原型/desktop-dark.png', fullPage: true });
    await tab.getByRole('button', { name: '浅色', exact: true }).click();
    check(await tab.locator('html').getAttribute('data-theme') === 'light', '浅色主题切换失败');
    await tab.screenshot({ path: 'test/2026-10-25_Chat分支原型/desktop-light.png', fullPage: true });
    await tab.setViewportSize({ width: 390, height: 844 });
    await tab.reload();
    await tab.locator('article').last().waitFor();
    check(await tab.locator('#inspector').isHidden(), '窄屏检查器应默认收起');
    const overflow = await tab.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    check(!overflow, '移动端存在横向溢出');
    await tab.locator('#toggle-inspector').click();
    check(await tab.locator('#inspector').isVisible(), '窄屏无法打开消息树');
    await tab.locator('#toggle-inspector').click();
    await tab.screenshot({ path: 'test/2026-10-25_Chat分支原型/mobile.png', fullPage: true });
    results.push('三主题切换、390px窄屏布局与消息树展开');

    check(errors.length === 0, `浏览器错误：${errors.join('; ')}`);
    return { passed: results.length, checks: results, browserErrors: errors, nodes: count(await history()) };
  } finally {
    await context.close();
  }
}
