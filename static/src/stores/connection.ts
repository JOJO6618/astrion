import { defineStore } from 'pinia';
import type { Socket } from 'socket.io-client';
import type { ReasoningEffort } from './personalization';

interface ConnectionState {
  isConnected: boolean;
  socket: Socket | null;
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
    socket: null,
    stopRequested: false,
    projectPath: '',
    agentVersion: '',
    thinkingMode: true,
    runMode: 'thinking',
    reasoningEffort: null
  }),
  actions: {
    setSocket(socket: Socket | null) {
      this.socket = socket;
    },
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
