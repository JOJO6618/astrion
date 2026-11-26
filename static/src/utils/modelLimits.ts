interface WireModelLimits {
  context_window?: unknown;
  default_context_window?: unknown;
  max_context_window?: unknown;
  max_input_tokens?: unknown;
  max_output_tokens?: unknown;
}

export interface ModelLimits {
  contextWindow?: number | null;
  defaultContextWindow?: number | null;
  maxContextWindow?: number | null;
  maxInputTokens?: number | null;
  maxOutputTokens?: number | null;
}

function positiveLimit(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

export function readModelLimits(model: WireModelLimits): ModelLimits {
  return {
    contextWindow: positiveLimit(model.context_window),
    defaultContextWindow: positiveLimit(model.default_context_window),
    maxContextWindow: positiveLimit(model.max_context_window),
    maxInputTokens: positiveLimit(model.max_input_tokens),
    maxOutputTokens: positiveLimit(model.max_output_tokens)
  };
}

export function modelContextCapacity(model: ModelLimits): number | null {
  return model.maxContextWindow ?? (model.defaultContextWindow == null ? (model.contextWindow ?? null) : null);
}

export function modelInputCapacity(model?: ModelLimits | null): number {
  const window = model?.contextWindow || 0;
  const input = model?.maxInputTokens || 0;
  return window && input ? Math.min(window, input) : Math.max(window, input);
}
