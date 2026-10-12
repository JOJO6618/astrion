// @ts-nocheck
import { composerDraftMethods } from './composerDrafts';

export const composerMethods = {
  ...composerDraftMethods,
  handleInputChange() {
    this.autoResizeInput();
    this.scheduleComposerDraftPersist('input-change');
  },
  handleInputFocus() {
    this.inputSetFocused(true);
    this.closeQuickMenu();
    this.closePermissionMenu();
  },
  handleInputBlur() {
    this.inputSetFocused(false);
  },
  getComposerElement(field) {
    const composer = this.getInputComposerRef();
    const unwrap = (value: any) => {
      if (!value) return null;
      if (value instanceof HTMLElement) return value;
      if (typeof value === 'object' && 'value' in value && !(value instanceof Window))
        return value.value;
      return value;
    };
    if (composer && composer[field]) return unwrap(composer[field]);
    if (this.$refs && this.$refs[field]) return unwrap(this.$refs[field]);
    return null;
  },
  getInputComposerRef() {
    return this.$refs.inputComposer || null;
  },
  handleComposerHeightChange(payload = {}) {
    const raw =
      Number(payload?.reservedHeight || 0) ||
      Number(payload?.height || 0) ||
      Number(payload?.composerHeight || 0);
    if (!Number.isFinite(raw) || raw <= 0) return;
    const next = Math.max(80, Math.min(560, Math.round(raw)));
    if (Math.abs(next - Number(this.composerReservedHeight || 0)) < 1) return;
    this.composerReservedHeight = next;
  }
};
