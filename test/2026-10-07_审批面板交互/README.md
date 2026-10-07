# 审批面板交互回归

本轮修改仅针对正式项目；用户明确不需要更新demo，本轮已写入的demo变更已从上一版压缩包恢复。

## 本轮改动

- 面板与待审批恢复按钮分别接入Vue Transition。面板使用与 `/` 菜单一致的300ms曲线，通过translateY和clip-path从底部向上露出、向下收回；恢复按钮上升出现、下降隐藏。
- 面板离场保留有效内容；离场层inert。最新用户纠正：形象、状态栏和待审批恢复按钮在开始收起时同时上升，不能等退场完成；审批和 `/` 菜单均已去掉该可见性等待。
- 完成后收起、不自动显示历史卡片，但保留最近快照，`/`与`+`设置手动入口可只读打开最近记录，没有记录显示“无记录”。空对话也可打开；重复终态不关闭手动查看，历史不包含执行按钮。
- 整张面板正常高度404px，约为原311px的130%；小窗口限制高度。人工模式上方内容可滚动，拒绝/允许固定在左下；自动审核时才增加右侧进度区域。

## 验证

```bash
node --test test/2026-10-07_审批面板交互/test_approval_motion_state.mjs
node --test test/2026-10-07_子智能体权限与单次完全访问/test_approval_state.mjs
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/eslint static/src/components/input/ComposerApprovalDock.vue static/src/components/panels/ToolApprovalPanel.vue static/src/components/input/InputComposer.vue static/src/app/methods/taskPolling/tool.ts --max-warnings 0
npm run build --silent 2>&1 | tail -n 5
```

首次实现时新增4项与原有11项状态回归通过。用户后续要求只直接构建，不再运行测试；本次已同步既有断言中历史保留和同步恢复的语义，但没有执行它们，不将此前通过结果等同于当前版本已测试。最新版本已通过项目正式构建链的tsc、stylelint和Vite构建，仍有原有的大chunk提示。

本轮未启动或重启服务，未运行Playwright或截图。动画观感、布局位置与小窗口效果由用户亲自验收。额外vue-tsc存在已知安装版本兼容问题，本轮未重试；通过的类型检查为项目正式构建链的tsc。

用户验收：2026-10-07，用户在最新入口与收起联动修正后实际测试，确认“测试下来没问题了”，并授权本次本地提交。该确认针对本轮审批界面，不扩展为Windows/Docker权限实机验收。
