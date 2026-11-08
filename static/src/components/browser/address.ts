export function displayBrowserAddress(value: string | undefined): string {
  if (!value || value === 'about:blank') return '';
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return value;
    const prefix = url.protocol === 'https:' ? '' : `${url.protocol}//`;
    const path = url.pathname.replace(/\/+$/, '');
    return `${prefix}${url.host}${path}${url.search}${url.hash}`;
  } catch {
    return value;
  }
}
