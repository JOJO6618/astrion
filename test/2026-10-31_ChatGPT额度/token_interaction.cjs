#!/usr/bin/env node
/**
 * ChatGPT 额度圆环交互回归测试入口（同一完整 18 项）。
 *
 * 交互块来自真实 InputComposer.vue script；SSR 用例在 token_card.cjs，
 * fixture/harness 在 token_fixture.cjs。运行本文件即跑全部用例。
 *
 * 运行：node test/2026-10-31_ChatGPT额度/token_interaction.cjs
 */
'use strict';

const { nextTick } = require('vue');
const { assert, composer, createHarness, pass } = require('./token_fixture.cjs');
const { testsCard } = require('./token_card.cjs');

async function testsInteractions() {
  console.log('交互块（真实 InputComposer script + 真实 Vue 响应式）');

  // 1. 鼠标 hover 开关
  {
    const harness = createHarness();
    const { api } = harness;
    assert.equal(api.chatgptQuotaVisible.value, true, 'ChatGPT 模型应可见额度卡');
    assert.equal(harness.active(), false, '未展开时 active 必须为 false');

    api.handleQuotaPointerEnter({ pointerType: 'mouse' });
    assert.equal(api.quotaCardOpen.value, true, '鼠标进入应展开');
    assert.equal(harness.active(), true, '展开后 active 必须为 true');
    await nextTick();
    assert.equal(harness.doc.count('pointerdown', true), 1, '展开后应注册外部点击监听');

    api.handleQuotaPointerLeave({ pointerType: 'mouse' });
    assert.equal(harness.timers.size, 1, '鼠标离开应安排延迟收起');
    harness.runTimers();
    assert.equal(api.quotaCardOpen.value, false, '延迟结束应收起');
    assert.equal(harness.active(), false, '收起后 active 必须为 false');
    await nextTick();
    assert.equal(harness.doc.count('pointerdown', true), 0, '收起后应移除外部点击监听');
    harness.unmount();
    pass('鼠标进入展开、离开延迟收起监听与 active 状态');
  }

  // 2. 普通模型（非 ChatGPT）也能展开额度卡
  {
    const harness = createHarness({
      model: {
        key: 'gpt-4o',
        label: 'GPT-4o',
        description: '',
        isChatgptModel: false,
        providerId: 'provider-openai'
      }
    });
    assert.equal(harness.api.chatgptQuotaVisible.value, true, '普通模型也应可展示额度卡');
    harness.api.handleQuotaPointerEnter({ pointerType: 'mouse' });
    assert.equal(harness.api.quotaCardOpen.value, true, '普通模型悬停应能展开额度卡');
    harness.api.handleQuotaPointerLeave({ pointerType: 'mouse' });
    harness.runTimers();
    assert.equal(harness.api.quotaCardOpen.value, false);
    harness.unmount();
    pass('普通模型（非 ChatGPT）同样可展开额度卡');
  }

  // 3. 无账号 / windows 为空时仍可展开（原 Token 信息在卡片内保留）
  {
    const harness = createHarness({ windows: [] });
    assert.equal(harness.api.chatgptQuotaVisible.value, true, '无账号时管理员仍可展开');
    harness.api.handleQuotaPointerEnter({ pointerType: 'mouse' });
    assert.equal(harness.api.quotaCardOpen.value, true, 'windows 为空也应能展开卡片');
    harness.unmount();
    pass('无账号 windows 为空时仍可展开');
  }

  // 4. 触摸 pointerenter 不应误展开
  {
    const harness = createHarness({ coarse: true });
    harness.api.handleQuotaPointerEnter({ pointerType: 'touch' });
    assert.equal(harness.api.quotaCardOpen.value, false, '触摸 pointerenter 不应展开');
    harness.unmount();
    pass('触摸 pointerenter 不触发 hover 展开');
  }

  // 5. 桌面点击保留原 emit
  {
    const harness = createHarness();
    harness.api.handleQuotaTriggerClick();
    assert.deepEqual(
      harness.emitted,
      ['toggle-token-panel'],
      '桌面点击应保留原 toggle-token-panel'
    );
    assert.equal(harness.api.quotaCardOpen.value, false, '桌面点击不展开额度卡');

    // 悬停已展开时点击：先收起，再走原 emit。
    harness.api.handleQuotaPointerEnter({ pointerType: 'mouse' });
    harness.api.handleQuotaTriggerClick();
    assert.equal(harness.api.quotaCardOpen.value, false, '桌面点击应关闭悬浮卡');
    assert.deepEqual(
      harness.emitted,
      ['toggle-token-panel', 'toggle-token-panel'],
      '桌面点击仍应再次 emit'
    );
    harness.unmount();
    pass('桌面点击保留原 toggle-token-panel');
  }

  // 6. Chat 模式桌面点击不 emit
  {
    const harness = createHarness({ chatMode: true });
    harness.api.handleQuotaTriggerClick();
    assert.deepEqual(harness.emitted, [], 'Chat 模式不应 emit toggle-token-panel');
    harness.unmount();
    pass('Chat 模式桌面点击不触发面板 emit');
  }

  // 7. 手机点击切换 + 不 emit
  {
    const harness = createHarness({ coarse: true });
    harness.api.handleQuotaTriggerClick();
    assert.equal(harness.api.quotaCardOpen.value, true, '手机点击应展开');
    assert.equal(harness.api.quotaCardPinned.value, true, '手机展开应保持 pinned');
    assert.deepEqual(harness.emitted, [], '手机点击不应 emit 面板切换');

    harness.api.handleQuotaTriggerClick();
    assert.equal(harness.api.quotaCardOpen.value, false, '手机再次点击应收起');
    assert.equal(harness.api.quotaCardPinned.value, false, '收起应清除 pinned');
    harness.unmount();
    pass('手机点击展开/收起（pinned），不 emit 面板切换');
  }

  // 8. 外部点击关闭，内部点击不关闭
  {
    const harness = createHarness({ coarse: true });
    const inside = { id: 'inside' };
    harness.api.quotaSwitcherEl.value = { contains: (node) => node === inside };
    harness.api.handleQuotaTriggerClick();
    await nextTick();
    assert.equal(harness.doc.count('pointerdown', true), 1, '应注册捕获阶段外部点击监听');

    harness.doc.dispatch('pointerdown', { target: inside });
    assert.equal(harness.api.quotaCardOpen.value, true, '卡片内部点击不应关闭');

    harness.doc.dispatch('pointerdown', { target: { id: 'outside' } });
    assert.equal(harness.api.quotaCardOpen.value, false, '外部点击应关闭');
    await nextTick();
    assert.equal(harness.doc.count('pointerdown', true), 0, '关闭后应移除外部点击监听');
    harness.unmount();
    pass('手机外部点击关闭、内部点击保持');
  }

  // 9. 键盘焦点：focusin 展开、focusout 收起；内部 relatedTarget 不收起
  {
    const harness = createHarness();
    const inside = { id: 'inside' };
    harness.api.quotaSwitcherEl.value = { contains: (node) => node === inside };

    harness.api.handleQuotaFocusIn();
    assert.equal(harness.api.quotaCardOpen.value, true, '键盘焦点进入应展开');

    harness.api.handleQuotaFocusOut({ relatedTarget: inside });
    assert.equal(harness.api.quotaCardOpen.value, true, '焦点仍在卡片内时不应收起');

    harness.api.handleQuotaFocusOut({ relatedTarget: null });
    assert.equal(harness.api.quotaCardOpen.value, false, '焦点离开应收起');
    harness.unmount();
    pass('键盘 focusin 展开、focusout 收起（内部焦点保持）');
  }

  // 10. Escape：真实模板绑定 + 关闭函数
  {
    const template = composer.template.content;
    assert.ok(
      template.includes('@keydown.esc.stop="closeQuotaCard()"'),
      '真实模板必须把 Escape 绑定到 closeQuotaCard'
    );
    assert.ok(template.includes('@focusin="handleQuotaFocusIn"'), '真实模板必须绑定 focusin');
    assert.ok(template.includes('@focusout="handleQuotaFocusOut"'), '真实模板必须绑定 focusout');
    assert.ok(template.includes('@click="handleQuotaTriggerClick"'), '真实模板必须绑定圆环点击');
    assert.ok(
      template.includes(':show-bars="quotaCardPinned"'),
      '真实模板必须把 pinned 状态传给卡片 showBars'
    );
    assert.ok(
      template.includes('context-usage-switcher__quota-popover--pinned'),
      '真实模板必须给 pinned 弹窗加宽态 class'
    );

    const harness = createHarness();
    harness.api.handleQuotaFocusIn();
    assert.equal(harness.api.quotaCardOpen.value, true);
    harness.api.closeQuotaCard();
    assert.equal(harness.api.quotaCardOpen.value, false, 'Escape 处理函数应关闭卡片');
    harness.unmount();
    pass('Escape 模板绑定存在且关闭卡片');
  }

  // 11. 非管理员 applicable=false：不展示额度卡，回落原 Token 卡
  {
    const harness = createHarness({ isAdmin: false });
    assert.equal(harness.quota.applicable.value, false, '非管理员 applicable 应为 false');
    assert.equal(harness.api.chatgptQuotaVisible.value, false, '非管理员不应展示额度卡');
    harness.api.handleQuotaPointerEnter({ pointerType: 'mouse' });
    assert.equal(harness.api.quotaCardOpen.value, false, '不可用时 hover 不应展开');
    harness.unmount();
    pass('普通用户 applicable=false 时回落原 Token 悬浮卡');
  }

  // 12. 卸载清理监听与定时器
  {
    const harness = createHarness();
    harness.api.handleQuotaPointerEnter({ pointerType: 'mouse' });
    await nextTick();
    assert.equal(harness.doc.count('pointerdown', true), 1);
    harness.api.handleQuotaPointerLeave({ pointerType: 'mouse' });
    assert.equal(harness.timers.size, 1);

    harness.unmount();
    assert.equal(harness.timers.size, 0, '卸载必须清理待执行收起定时器');
    assert.equal(harness.doc.count('pointerdown', true), 0, '卸载必须移除外部点击监听');
    pass('卸载清理外部监听与收起定时器');
  }
}

(async () => {
  try {
    await testsInteractions();
    await testsCard();
    console.log('\n全部通过：额度交互块 + ChatgptQuotaCard SSR');
  } catch (error) {
    console.error('\n测试失败：');
    console.error(error);
    process.exitCode = 1;
  }
})();
