export default {
  title: '目标模式',
  target: '目标',
  placeholder: '描述希望达成的目标，以及必要的验收要求…',
  start: '开始',
  resume: '继续',
  closeGoal: '关闭目标模式',
  pause: '暂停',
  newGoal: '新目标',
  advanced: '运行限制（可选）',
  maxTurns: '最多推进轮数',
  maxTokens: 'Token 上限',
  turns: '推进轮数',
  tokens: '已用 Token',
  retryAt: '下次重试：{time}',
  resumeMessage:
    '继续当前目标。请从已保存的进度推进，先核实已完成的操作，避免重复执行。\n目标：{goal}',
  actionFailed: '操作失败，请重试',
  invalidLimit: '请输入有效的正整数限制',
  states: {
    running: '执行中',
    reviewing: '检查中',
    retrying: '等待重试',
    paused: '已暂停',
    blocked: '已阻塞',
    done: '已完成',
    stopped: '已关闭'
  },
  reasons: {
    user_cancel: '已手动暂停，可点击继续。',
    process_restarted: '应用已重启，可点击继续恢复目标。',
    max_turns: '已达到本次轮数上限，可调整限制后继续。',
    max_tokens: '已达到本次 Token 上限，可调整限制后继续。',
    no_progress: '连续多轮没有取得新进展。',
    execution_interrupted: '执行已中断，可点击继续。',
    quota_exhausted: '额度不足，恢复后可点击继续。',
    authentication: '认证失败，修正后可点击继续。',
    configuration: '模型或请求配置异常，修正后可点击继续。'
  }
};
