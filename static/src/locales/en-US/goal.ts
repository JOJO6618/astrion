export default {
  title: 'Goal mode',
  target: 'Goal',
  placeholder: 'Describe the goal and any acceptance requirements…',
  start: 'Start',
  resume: 'Continue',
  closeGoal: 'Close goal mode',
  pause: 'Pause',
  newGoal: 'New goal',
  advanced: 'Run limits (optional)',
  maxTurns: 'Maximum progress rounds',
  maxTokens: 'Token limit',
  turns: 'Progress rounds',
  tokens: 'Tokens used',
  retryAt: 'Next retry: {time}',
  resumeMessage:
    'Continue the current goal from saved progress. Verify completed operations before proceeding to avoid duplicate actions.\nGoal: {goal}',
  actionFailed: 'Action failed. Please try again.',
  invalidLimit: 'Enter a valid positive integer limit.',
  states: {
    running: 'Running',
    reviewing: 'Reviewing',
    retrying: 'Waiting to retry',
    paused: 'Paused',
    blocked: 'Blocked',
    done: 'Completed',
    stopped: 'Closed'
  },
  reasons: {
    user_cancel: 'Paused manually. Continue when ready.',
    process_restarted: 'The app restarted. Continue to resume this goal.',
    max_turns: 'Round limit reached. Adjust the limit and continue.',
    max_tokens: 'Token limit reached. Adjust the limit and continue.',
    no_progress: 'No new progress over several rounds.',
    execution_interrupted: 'Execution interrupted. You can continue.',
    quota_exhausted: 'Quota exhausted. Continue after it becomes available.',
    authentication: 'Authentication failed. Correct it and continue.',
    configuration: 'Model or request configuration failed. Correct it and continue.'
  }
};
