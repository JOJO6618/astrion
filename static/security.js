(function () {
    if (window.__secureFetchWrapped) {
        return;
    }
    if (typeof window.fetch !== 'function') {
        return;
    }

    window.__secureFetchWrapped = true;
    const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);
    const originalFetch = window.fetch.bind(window);
    const csrfState = {
        token: null,
        inflight: null
    };

    async function fetchCsrfToken(forceRefresh = false) {
        if (!forceRefresh && csrfState.token) {
            return csrfState.token;
        }
        if (csrfState.inflight && !forceRefresh) {
            return csrfState.inflight;
        }
        csrfState.inflight = originalFetch('/api/csrf-token', { credentials: 'same-origin' })
            .then((resp) => {
                if (!resp.ok) {
                    throw new Error('无法获取 CSRF token');
                }
                return resp.json();
            })
            .then((data) => {
                csrfState.token = data && data.token ? data.token : '';
                csrfState.inflight = null;
                return csrfState.token;
            })
            .catch((err) => {
                csrfState.inflight = null;
                csrfState.token = null;
                throw err;
            });
        return csrfState.inflight;
    }

    function extractMethod(resource, options) {
        if (options && options.method) {
            return options.method;
        }
        const RequestCtor = typeof Request !== 'undefined' ? Request : null;
        if (RequestCtor && resource instanceof RequestCtor && resource.method) {
            return resource.method;
        }
        return 'GET';
    }

    function mergeHeaders(baseHeaders, extraHeaders) {
        const merged = new Headers(baseHeaders || {});
        if (extraHeaders) {
            new Headers(extraHeaders).forEach((value, key) => merged.set(key, value));
        }
        return merged;
    }

    async function secureFetch(resource, init) {
        const RequestCtor = typeof Request !== 'undefined' ? Request : null;
        let requestInfo = resource;
        let options = init ? { ...init } : {};
        const method = (extractMethod(resource, options) || 'GET').toUpperCase();
        const needsProtection = !SAFE_METHODS.has(method);

        if (needsProtection) {
            const token = await fetchCsrfToken();
            if (RequestCtor && resource instanceof RequestCtor) {
                const merged = mergeHeaders(resource.headers, options.headers);
                merged.set('X-CSRF-Token', token);
                requestInfo = new RequestCtor(resource, { headers: merged });
                if (options.headers) {
                    options = { ...options };
                    delete options.headers;
                }
            } else {
                const headers = mergeHeaders(null, options.headers);
                headers.set('X-CSRF-Token', token);
                options.headers = headers;
            }
        }
        return originalFetch(requestInfo, options);
    }

    async function requestSocketToken() {
        const resp = await originalFetch('/api/socket-token', { credentials: 'same-origin' });
        if (!resp.ok) {
            throw new Error('无法获取实时连接凭证');
        }
        const data = await resp.json();
        if (!data || !data.success || !data.token) {
            throw new Error((data && data.error) || '实时连接凭证无效');
        }
        return data.token;
    }

    window.fetch = function (resource, init) {
        return secureFetch(resource, init);
    };

    window.ensureCsrfToken = fetchCsrfToken;
    window.refreshCsrfToken = () => fetchCsrfToken(true);
    window.requestSocketToken = requestSocketToken;
    window.secureFetch = secureFetch;
})();
