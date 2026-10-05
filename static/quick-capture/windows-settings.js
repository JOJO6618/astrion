(() => {
  const request = async (op, body) => {
    const response = await fetch(`/api/desktop/quick/${op}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    });
    const result = await response.json();
    if (!response.ok || !result.ok)
      throw new Error(result.error || `Quick settings HTTP ${response.status}`);
    return result.data;
  };
  window.astrionQuickSettings = {
    info: () => request('info'),
    configure: (patch) => request('configure', patch),
    permissions: () => request('permissions'),
    open: () => request('open', {}),
    capturePermission: () => request('capture-permission', {}),
    inputPermission: () => request('input-permission', {})
  };
})();
