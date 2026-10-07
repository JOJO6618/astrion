// Persisted messages and runtime events can carry ISO strings, seconds or milliseconds.
export function displayMessageTime(value: unknown): string | null {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    const milliseconds = value < 100_000_000_000 ? value * 1000 : value;
    return new Date(milliseconds).toISOString();
  }
  return typeof value === 'string' && value ? value : null;
}
