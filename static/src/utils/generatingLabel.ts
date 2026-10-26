import { t } from '@/locales';

const GENERATING_LABEL_KEYS = [
  'stores.generatingBrainstorming',
  'stores.generatingPreparing',
  'stores.generatingPreparingTools',
  'stores.generatingThink',
  'stores.generatingAnswerSoon',
  'stores.generatingLoadingInspiration',
  'stores.generatingAssemblingIdeas',
  'stores.generatingBestApproach',
  'stores.generatingBrainMeeting',
  'stores.generatingOrganizing',
  'stores.generatingPolishing',
  'stores.generatingContext',
  'stores.generatingMemorySearch',
  'stores.generatingAlmostDone',
  'stores.generatingDomainExpand',
  'stores.generatingToolchain',
  'stores.generatingSentence',
  'stores.generatingRethinking',
  'stores.generatingKnowledgeSearch'
];

export function randomGeneratingLabel() {
  return t(GENERATING_LABEL_KEYS[Math.floor(Math.random() * GENERATING_LABEL_KEYS.length)]);
}

/** Keep a placeholder's label stable across authoritative snapshot replacements. */
export function prepareGeneratingLabels(messages: any[], previous: any[] = []) {
  const labels = new Map(previous.map((message) => [message.id, message.generatingLabel]));
  for (const message of messages) {
    if (message.role !== 'assistant' || !message.awaitingFirstContent) continue;
    message.generatingLabel ||= labels.get(message.id) || randomGeneratingLabel();
  }
}
