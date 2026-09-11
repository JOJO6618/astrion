import { defineStore } from 'pinia';
import type { ReasoningEffort } from './personalization';

interface ConnectionState {
  isConnected: boolean;
  stopRequested: boolean;
  projectPath: string;
  agentVersion: string;
  thinkingMode: boolean;
  runMode: 'fast' | 'thinking';
  reasoningEffort: ReasoningEffort | null;
}

export const useConnectionStore = defineStore('connection', {
  state: (): ConnectionState => ({
    isConnected: false,
    stopRequested: false,
    projectPath: '',
    agentVersion: '',
    thinkingMode: true,
    runMode: 'thinking',
    reasoningEffort: null
  }),
  actions: {
    setConnected(value: boolean) {
      this.isConnected = value;
    },
    setStopRequested(value: boolean) {
      this.stopRequested = value;
    },
    requestStop() {
      this.stopRequested = true;
    },
    clearStopRequest() {
      this.stopRequested = false;
    },
    setProjectPath(path: string) {
      this.projectPath = path;
    },
    setAgentVersion(version: string) {
      this.agentVersion = version;
    },
    setThinkingMode(value: boolean) {
      this.thinkingMode = !!value;
    },
    toggleThinkingMode() {
      this.thinkingMode = !this.thinkingMode;
    },
    setRunMode(mode: 'fast' | 'thinking') {
      this.runMode = mode;
      this.thinkingMode = mode !== 'fast';
    },
    setReasoningEffort(effort: ReasoningEffort | null) {
      this.reasoningEffort = effort;
    }
  }
});
