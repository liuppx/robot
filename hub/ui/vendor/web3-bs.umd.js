(function (global, factory) {
    typeof exports === 'object' && typeof module !== 'undefined' ? factory(exports) :
    typeof define === 'function' && define.amd ? define(['exports'], factory) :
    (global = typeof globalThis !== 'undefined' ? globalThis : global || self, factory(global.YeYingWeb3 = {}));
})(this, (function (exports) { 'use strict';

    const YEYING_RDNS = 'io.github.yeying';
    const DEFAULT_TIMEOUT = 1000;
    const DEFAULT_ACCOUNT_STORAGE_KEY = 'yeying:last_account';
    const DEFAULT_PROVIDER_POLL_INTERVAL = 100;
    const DEFAULT_PROVIDER_MAX_POLLS = 20;
    const requestAccountsInFlight = new WeakMap();
    function isProvider(value) {
        return !!value && typeof value.request === 'function';
    }
    function getWindowEthereum() {
        if (typeof window === 'undefined')
            return null;
        const ethereum = window.ethereum;
        return isProvider(ethereum) ? ethereum : null;
    }
    function getWindowProviderCandidates() {
        if (typeof window === 'undefined')
            return [];
        const source = window;
        const candidates = [];
        const addProvider = (provider) => {
            if (isProvider(provider) && !candidates.includes(provider)) {
                candidates.push(provider);
            }
        };
        for (const name of [
            'ethereum',
            'yeeying',
            'yeying',
            'coinbaseWallet',
            'bitkeep',
            'tokenpocket',
            '__YEYING_PROVIDER__',
        ]) {
            addProvider(source[name]);
        }
        const ethereum = getWindowEthereum();
        if (Array.isArray(ethereum?.providers)) {
            for (const provider of ethereum.providers) {
                addProvider(provider);
            }
        }
        return candidates;
    }
    function readStoredAccount(storageKey) {
        if (typeof localStorage === 'undefined')
            return null;
        try {
            return localStorage.getItem(storageKey);
        }
        catch {
            return null;
        }
    }
    function writeStoredAccount(storageKey, account) {
        if (typeof localStorage === 'undefined')
            return;
        try {
            if (account) {
                localStorage.setItem(storageKey, account);
            }
            else {
                localStorage.removeItem(storageKey);
            }
        }
        catch {
            // ignore storage errors
        }
    }
    function selectPreferredAccount(accounts, stored, preferStored) {
        if (preferStored && stored && accounts.includes(stored)) {
            return stored;
        }
        return accounts[0] || null;
    }
    function isYeYingProvider(provider, info) {
        if (!provider)
            return false;
        if (provider.isYeYing)
            return true;
        const name = (info?.name || '').toLowerCase();
        const rdns = (info?.rdns || '').toLowerCase();
        return rdns === YEYING_RDNS || name.includes('yeying');
    }
    function selectBestProvider(candidates, preferYeYing) {
        if (candidates.length === 0)
            return selectBestWindowProvider(preferYeYing);
        if (preferYeYing) {
            const yeying = candidates.find(c => isYeYingProvider(c.provider, c.info));
            if (yeying)
                return yeying.provider;
        }
        return candidates[0].provider;
    }
    function selectBestWindowProvider(preferYeYing) {
        const candidates = getWindowProviderCandidates();
        if (candidates.length === 0)
            return null;
        if (preferYeYing) {
            const yeying = candidates.find(provider => isYeYingProvider(provider));
            if (yeying)
                return yeying;
        }
        return candidates[0];
    }
    async function getProvider(options = {}) {
        const preferYeYing = options.preferYeYing !== false;
        const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT;
        const windowProvider = selectBestWindowProvider(preferYeYing);
        if (preferYeYing && isYeYingProvider(windowProvider)) {
            return windowProvider;
        }
        if (typeof window === 'undefined') {
            return windowProvider;
        }
        const discovered = [];
        let resolved = false;
        return await new Promise(resolve => {
            const cleanup = () => {
                window.removeEventListener('eip6963:announceProvider', onAnnounce);
                window.removeEventListener('ethereum#initialized', onEthereumInitialized);
                if (timeoutId)
                    clearTimeout(timeoutId);
            };
            const safeResolve = (provider) => {
                if (resolved)
                    return;
                resolved = true;
                cleanup();
                resolve(provider);
            };
            const onAnnounce = (event) => {
                const detail = event.detail;
                if (!detail?.provider)
                    return;
                discovered.push(detail);
                if (preferYeYing && isYeYingProvider(detail.provider, detail.info)) {
                    safeResolve(detail.provider);
                }
            };
            const onEthereumInitialized = () => {
                const injected = selectBestWindowProvider(preferYeYing);
                if (preferYeYing && isYeYingProvider(injected)) {
                    safeResolve(injected);
                }
            };
            window.addEventListener('eip6963:announceProvider', onAnnounce);
            window.addEventListener('ethereum#initialized', onEthereumInitialized, { once: true });
            const timeoutId = setTimeout(() => {
                if (resolved)
                    return;
                const best = selectBestProvider(discovered, preferYeYing) ||
                    windowProvider ||
                    selectBestWindowProvider(preferYeYing);
                safeResolve(best || null);
            }, timeoutMs);
            try {
                window.dispatchEvent(new Event('eip6963:requestProvider'));
            }
            catch {
                // Ignore if browser doesn't support CustomEvent target
            }
            if (!preferYeYing && windowProvider) {
                safeResolve(windowProvider);
            }
        });
    }
    function watchProvider(handler, options = {}) {
        if (typeof window === 'undefined') {
            handler({ provider: null, present: false });
            return () => { };
        }
        const preferYeYing = options.preferYeYing !== false;
        const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_PROVIDER_POLL_INTERVAL;
        const maxPolls = options.maxPolls ?? DEFAULT_PROVIDER_MAX_POLLS;
        let stopped = false;
        let lastProvider;
        let pollCount = 0;
        let pollTimer = null;
        const emit = () => {
            if (stopped)
                return;
            const provider = selectBestWindowProvider(preferYeYing);
            if (provider === lastProvider)
                return;
            lastProvider = provider;
            handler({ provider, present: !!provider });
        };
        const poll = () => {
            if (stopped)
                return;
            emit();
            pollCount += 1;
            if (lastProvider || pollCount >= maxPolls)
                return;
            pollTimer = setTimeout(poll, pollIntervalMs);
        };
        const handleProviderReady = () => {
            emit();
        };
        window.addEventListener('ethereum#initialized', handleProviderReady);
        window.addEventListener('eip6963:announceProvider', handleProviderReady);
        try {
            window.dispatchEvent(new Event('eip6963:requestProvider'));
        }
        catch {
            // Ignore unsupported event dispatch environments.
        }
        poll();
        return () => {
            stopped = true;
            if (pollTimer) {
                clearTimeout(pollTimer);
            }
            window.removeEventListener('ethereum#initialized', handleProviderReady);
            window.removeEventListener('eip6963:announceProvider', handleProviderReady);
        };
    }
    function getWalletErrorMessage(error) {
        if (!error)
            return '';
        if (typeof error === 'string')
            return error;
        if (error instanceof Error)
            return error.message || String(error);
        const message = error.message;
        if (typeof message === 'string')
            return message;
        return String(error);
    }
    function getWalletErrorCode(error) {
        const code = Number(error?.code);
        if (!Number.isNaN(code))
            return code;
        const causeCode = Number(error?.cause?.code);
        if (!Number.isNaN(causeCode))
            return causeCode;
        return null;
    }
    function classifyWalletError(error) {
        const code = getWalletErrorCode(error);
        const message = getWalletErrorMessage(error);
        const lowerMessage = message.toLowerCase();
        if (code === 4001 || lowerMessage.includes('user rejected')) {
            return { type: 'userRejected', code, message };
        }
        if (code === 4900 ||
            lowerMessage.includes('disconnected') ||
            lowerMessage.includes('reconnect') ||
            lowerMessage.includes('not connected')) {
            return { type: 'disconnected', code, message };
        }
        if (lowerMessage.includes('timeout')) {
            return { type: 'timeout', code, message };
        }
        if (lowerMessage.includes('no injected wallet provider') ||
            lowerMessage.includes('未检测到钱包')) {
            return { type: 'notFound', code, message };
        }
        return { type: 'unknown', code, message };
    }
    function isUserRejectedWalletAction(error) {
        return classifyWalletError(error).type === 'userRejected';
    }
    function isWalletReconnectError(error) {
        const type = classifyWalletError(error).type;
        return type === 'disconnected' || type === 'timeout';
    }
    async function requireProvider(options = {}) {
        const provider = await getProvider(options);
        if (!provider) {
            throw new Error('No injected wallet provider found');
        }
        return provider;
    }
    async function requestAccounts(options = {}) {
        const provider = options.provider || (await requireProvider());
        const dedupe = options.dedupe !== false;
        if (dedupe) {
            const pending = requestAccountsInFlight.get(provider);
            if (pending)
                return pending;
        }
        const request = provider.request({
            method: 'eth_requestAccounts',
        }).then(accounts => (Array.isArray(accounts) ? accounts : []));
        if (!dedupe)
            return request;
        requestAccountsInFlight.set(provider, request);
        try {
            return await request;
        }
        finally {
            requestAccountsInFlight.delete(provider);
        }
    }
    async function focusPendingApproval(provider) {
        const p = provider || (await requireProvider());
        const result = await p.request({
            method: 'wallet_focusPendingApproval',
        });
        if (!result || typeof result !== 'object') {
            return { focused: false, type: null };
        }
        const payload = result;
        return {
            focused: Boolean(payload.focused),
            type: typeof payload.type === 'string' ? payload.type : null,
            requestId: typeof payload.requestId === 'string' ? payload.requestId : null,
            origin: typeof payload.origin === 'string' ? payload.origin : '',
            tabId: typeof payload.tabId === 'number' && Number.isFinite(payload.tabId)
                ? payload.tabId
                : null,
        };
    }
    async function getAccounts(provider) {
        const p = provider || (await requireProvider());
        const accounts = (await p.request({ method: 'eth_accounts' }));
        return Array.isArray(accounts) ? accounts : [];
    }
    async function getChainId(provider) {
        const p = provider || (await requireProvider());
        const chainId = (await p.request({ method: 'eth_chainId' }));
        return typeof chainId === 'string' ? chainId : null;
    }
    async function getPreferredAccount(options = {}) {
        const provider = options.provider || (await requireProvider());
        const storageKey = options.storageKey || DEFAULT_ACCOUNT_STORAGE_KEY;
        const preferStored = options.preferStored !== false;
        let accounts = await getAccounts(provider);
        if (accounts.length === 0 && options.autoConnect) {
            accounts = await requestAccounts({ provider });
        }
        const stored = readStoredAccount(storageKey);
        const account = selectPreferredAccount(accounts, stored, preferStored);
        writeStoredAccount(storageKey, account);
        return { account, accounts };
    }
    function watchAccounts(provider, handler, options = {}) {
        const storageKey = options.storageKey || DEFAULT_ACCOUNT_STORAGE_KEY;
        const preferStored = options.preferStored !== false;
        return onAccountsChanged(provider, (accounts) => {
            const stored = readStoredAccount(storageKey);
            const account = selectPreferredAccount(accounts, stored, preferStored);
            writeStoredAccount(storageKey, account);
            handler({ account, accounts });
        });
    }
    async function getBalance(provider, address, blockTag = 'latest') {
        const p = provider || (await requireProvider());
        let target = address;
        if (!target) {
            const accounts = await getAccounts(p);
            target = accounts[0];
        }
        if (!target) {
            throw new Error('No account available for balance');
        }
        const balance = (await p.request({
            method: 'eth_getBalance',
            params: [target, blockTag],
        }));
        if (typeof balance !== 'string') {
            throw new Error('Invalid balance response');
        }
        return balance;
    }
    function onAccountsChanged(provider, handler) {
        provider.on?.('accountsChanged', handler);
        return () => provider.removeListener?.('accountsChanged', handler);
    }
    function onChainChanged(provider, handler) {
        provider.on?.('chainChanged', handler);
        return () => provider.removeListener?.('chainChanged', handler);
    }

    function normalizeBaseUrl$2(baseUrl) {
        return baseUrl.replace(/\/+$/, '');
    }
    function joinUrl$2(baseUrl, path) {
        const trimmed = path.replace(/^\/+/, '');
        return `${normalizeBaseUrl$2(baseUrl)}/${trimmed}`;
    }
    const DEFAULT_TOKEN_KEY = 'authToken';
    let cachedAccessToken = null;
    let refreshInFlight = null;
    function resolveTokenKey(options) {
        return options?.tokenStorageKey || DEFAULT_TOKEN_KEY;
    }
    function shouldStoreToken(options) {
        return options?.storeToken !== false;
    }
    function resolveFetcher$1(options) {
        return options?.fetcher || fetch;
    }
    function resolveCredentials$1(options) {
        return options?.credentials ?? 'include';
    }
    function readStoredToken(options) {
        if (!shouldStoreToken(options))
            return null;
        if (typeof localStorage === 'undefined')
            return null;
        const key = resolveTokenKey(options);
        return localStorage.getItem(key);
    }
    function persistToken(token, options) {
        cachedAccessToken = token;
        if (!shouldStoreToken(options))
            return;
        if (typeof localStorage === 'undefined')
            return;
        const key = resolveTokenKey(options);
        if (!token) {
            localStorage.removeItem(key);
        }
        else {
            localStorage.setItem(key, token);
        }
    }
    function getAccessToken(options) {
        if (cachedAccessToken)
            return cachedAccessToken;
        const stored = readStoredToken(options);
        if (stored) {
            cachedAccessToken = stored;
        }
        return stored;
    }
    function setAccessToken(token, options) {
        persistToken(token, options);
    }
    function clearAccessToken(options) {
        cachedAccessToken = null;
        if (typeof localStorage === 'undefined')
            return;
        const key = resolveTokenKey(options);
        localStorage.removeItem(key);
    }
    async function resolveAddress$1(provider, address) {
        if (address)
            return address;
        let accounts = await getAccounts(provider);
        if (!accounts[0]) {
            const requested = (await provider.request({
                method: 'eth_requestAccounts',
            }));
            if (Array.isArray(requested)) {
                accounts = requested;
            }
        }
        if (!accounts[0]) {
            throw new Error('No account available');
        }
        return accounts[0];
    }
    function extractChallenge(payload) {
        if (!payload || typeof payload !== 'object')
            return null;
        const data = payload;
        const envelope = data.data;
        if (envelope) {
            const value = envelope.challenge;
            if (typeof value === 'string')
                return value;
        }
        const direct = data.challenge || data.result;
        if (typeof direct === 'string')
            return direct;
        if (direct && typeof direct === 'object') {
            const nested = direct.challenge;
            if (typeof nested === 'string')
                return nested;
        }
        const body = data.body;
        if (body) {
            const bodyResult = body.result;
            if (typeof bodyResult === 'string')
                return bodyResult;
            if (bodyResult && typeof bodyResult === 'object') {
                const nested = bodyResult.challenge;
                if (typeof nested === 'string')
                    return nested;
            }
        }
        return null;
    }
    function extractToken(payload) {
        if (!payload || typeof payload !== 'object')
            return null;
        const data = payload;
        const envelope = data.data;
        if (envelope) {
            const value = envelope.token;
            if (typeof value === 'string')
                return value;
        }
        const direct = data.token || data.result;
        if (typeof direct === 'string')
            return direct;
        const body = data.body;
        if (body) {
            const bodyToken = body.token;
            if (typeof bodyToken === 'string')
                return bodyToken;
            const bodyResult = body.result;
            if (typeof bodyResult === 'string')
                return bodyResult;
            if (bodyResult && typeof bodyResult === 'object') {
                const nested = bodyResult.token;
                if (typeof nested === 'string')
                    return nested;
            }
        }
        return null;
    }
    async function signMessage(options) {
        const provider = options.provider || (await requireProvider());
        const address = await resolveAddress$1(provider, options.address);
        const method = options.method || 'personal_sign';
        const params = method === 'eth_sign'
            ? [address, options.message]
            : [options.message, address];
        const signature = await provider.request({
            method,
            params,
        });
        if (typeof signature !== 'string') {
            throw new Error('Invalid signature response');
        }
        return signature;
    }
    async function loginWithChallenge(options = {}) {
        const provider = options.provider || (await requireProvider());
        const address = await resolveAddress$1(provider, options.address);
        const fetcher = resolveFetcher$1(options);
        const credentials = resolveCredentials$1(options);
        const baseUrl = options.baseUrl || '/api/v1/public/auth';
        const challengeUrl = joinUrl$2(baseUrl, options.challengePath || 'challenge');
        const verifyUrl = joinUrl$2(baseUrl, options.verifyPath || 'verify');
        const challengeBody = {
            address,
        };
        const challengeRes = await fetcher(challengeUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                accept: 'application/json',
            },
            credentials,
            body: JSON.stringify(challengeBody),
        });
        if (!challengeRes.ok) {
            const text = await challengeRes.text();
            throw new Error(`Challenge request failed: ${challengeRes.status} ${text}`);
        }
        const challengePayload = await challengeRes.json();
        const challenge = extractChallenge(challengePayload);
        if (!challenge) {
            throw new Error('Challenge response missing challenge');
        }
        const signature = await signMessage({
            provider,
            address,
            message: challenge,
            method: options.signMethod || 'personal_sign',
        });
        const verifyBody = {
            address,
            signature,
        };
        const verifyRes = await fetcher(verifyUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                accept: 'application/json',
            },
            credentials,
            body: JSON.stringify(verifyBody),
        });
        if (!verifyRes.ok) {
            const text = await verifyRes.text();
            throw new Error(`Verify request failed: ${verifyRes.status} ${text}`);
        }
        const verifyPayload = await verifyRes.json();
        const token = extractToken(verifyPayload);
        if (!token) {
            throw new Error('Verify response missing token');
        }
        persistToken(token, options);
        return {
            token,
            address,
            signature,
            challenge,
            response: verifyPayload,
        };
    }
    async function refreshAccessToken(options = {}) {
        if (refreshInFlight) {
            return refreshInFlight;
        }
        const task = (async () => {
            const fetcher = resolveFetcher$1(options);
            const credentials = resolveCredentials$1(options);
            const baseUrl = options.baseUrl || '/api/v1/public/auth';
            const refreshUrl = joinUrl$2(baseUrl, options.refreshPath || 'refresh');
            const refreshRes = await fetcher(refreshUrl, {
                method: 'POST',
                headers: {
                    accept: 'application/json',
                },
                credentials,
            });
            if (!refreshRes.ok) {
                const text = await refreshRes.text();
                throw new Error(`Refresh request failed: ${refreshRes.status} ${text}`);
            }
            const refreshPayload = await refreshRes.json();
            const token = extractToken(refreshPayload);
            if (!token) {
                throw new Error('Refresh response missing token');
            }
            persistToken(token, options);
            return { token, response: refreshPayload };
        })();
        refreshInFlight = task;
        try {
            return await task;
        }
        finally {
            refreshInFlight = null;
        }
    }
    async function logout(options = {}) {
        const fetcher = resolveFetcher$1(options);
        const credentials = resolveCredentials$1(options);
        const baseUrl = options.baseUrl || '/api/v1/public/auth';
        const logoutUrl = joinUrl$2(baseUrl, options.logoutPath || 'logout');
        const logoutRes = await fetcher(logoutUrl, {
            method: 'POST',
            headers: {
                accept: 'application/json',
            },
            credentials,
        });
        if (!logoutRes.ok) {
            const text = await logoutRes.text();
            throw new Error(`Logout request failed: ${logoutRes.status} ${text}`);
        }
        let payload = null;
        try {
            payload = await logoutRes.json();
        }
        catch {
            payload = null;
        }
        clearAccessToken(options);
        return { response: payload };
    }
    async function authFetch(input, init = {}, options = {}) {
        const fetcher = resolveFetcher$1(options);
        const credentials = resolveCredentials$1(options);
        const retryOnUnauthorized = options.retryOnUnauthorized !== false;
        const performRequest = async (tokenOverride) => {
            const headers = new Headers(init.headers || {});
            const token = tokenOverride ?? options.accessToken ?? getAccessToken(options);
            if (token && !headers.has('Authorization')) {
                headers.set('Authorization', `Bearer ${token}`);
            }
            return fetcher(input, {
                ...init,
                headers,
                credentials,
            });
        };
        const initialRes = await performRequest();
        if (initialRes.status !== 401 || !retryOnUnauthorized) {
            return initialRes;
        }
        try {
            const refreshed = await refreshAccessToken(options);
            return await performRequest(refreshed.token);
        }
        catch {
            return initialRes;
        }
    }

    const DEFAULT_SESSION_ID = 'default';
    const DEFAULT_UCAN_SESSION_TTL_MS = 24 * 60 * 60 * 1000;
    const DEFAULT_UCAN_TOKEN_TTL_MS = 40 * 60 * 1000;
    const DEFAULT_UCAN_TOKEN_SKEW_MS = 60 * 1000;
    const DB_NAME = 'yeying-web3';
    const DB_STORE = 'ucan-sessions';
    const BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
    const DID_KEY_ED25519_MULTICODEC = new Uint8Array([0xed, 0x01]);
    const textEncoder = new TextEncoder();
    function toBase64Url(data) {
        const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 1) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }
    function normalizeActionExpression(raw) {
        const normalized = String(raw || '').trim().toLowerCase().replace(/\|/g, ',');
        if (!normalized)
            return '';
        const parts = normalized
            .split(',')
            .map(part => part.trim())
            .filter(Boolean);
        if (!parts.length)
            return '';
        return Array.from(new Set(parts)).join(',');
    }
    function getCapabilityResource(cap) {
        if (!cap || typeof cap !== 'object')
            return '';
        const withValue = typeof cap.with === 'string' ? cap.with.trim() : '';
        if (withValue)
            return withValue;
        return typeof cap.resource === 'string' ? cap.resource.trim() : '';
    }
    function getCapabilityAction(cap) {
        if (!cap || typeof cap !== 'object')
            return '';
        const canValue = typeof cap.can === 'string' ? cap.can.trim() : '';
        if (canValue)
            return normalizeActionExpression(canValue);
        const actionValue = typeof cap.action === 'string' ? cap.action.trim() : '';
        return normalizeActionExpression(actionValue);
    }
    function normalizeUcanCapability(cap, options = {}) {
        const includeLegacyAliases = options.includeLegacyAliases !== false;
        const resource = getCapabilityResource(cap);
        const action = getCapabilityAction(cap);
        if (!resource || !action)
            return null;
        const normalized = {
            with: resource,
            can: action,
        };
        if (includeLegacyAliases) {
            normalized.resource = resource;
            normalized.action = action;
        }
        if (cap && Object.prototype.hasOwnProperty.call(cap, 'nb')) {
            normalized.nb = cap.nb;
        }
        return normalized;
    }
    function normalizeUcanCapabilities(caps, options = {}) {
        const includeLegacyAliases = options.includeLegacyAliases !== false;
        const seen = new Set();
        const result = [];
        for (const cap of caps || []) {
            const normalized = normalizeUcanCapability(cap, { includeLegacyAliases });
            if (!normalized)
                continue;
            const key = `${normalized.with}|${normalized.can}`;
            if (seen.has(key))
                continue;
            seen.add(key);
            result.push(normalized);
        }
        return result;
    }
    function encodeJson(value) {
        return toBase64Url(textEncoder.encode(JSON.stringify(value)));
    }
    function decodeBase64Url(input) {
        if (!input)
            return null;
        const base64 = input.replace(/-/g, '+').replace(/_/g, '/');
        const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, '=');
        try {
            if (typeof atob === 'function') {
                return atob(padded);
            }
        }
        catch {
            // Try Node-compatible fallback below.
        }
        try {
            const nodeBuffer = globalThis.Buffer;
            if (nodeBuffer) {
                return nodeBuffer.from(padded, 'base64').toString('utf8');
            }
        }
        catch {
            return null;
        }
        return null;
    }
    function randomNonce(bytes = 16) {
        const buffer = new Uint8Array(bytes);
        crypto.getRandomValues(buffer);
        return Array.from(buffer)
            .map(b => b.toString(16).padStart(2, '0'))
            .join('');
    }
    function normalizeExpiry(exp, fallbackMs) {
        if (typeof exp === 'number' && !Number.isNaN(exp))
            return exp;
        return Date.now() + fallbackMs;
    }
    function normalizeUcanExpiry(exp, fallbackMs) {
        return normalizeExpiry(exp, fallbackMs);
    }
    function decodeUcanPayload(token) {
        const parts = String(token || '').split('.');
        if (parts.length < 2)
            return null;
        const decoded = decodeBase64Url(parts[1]);
        if (!decoded)
            return null;
        try {
            const payload = JSON.parse(decoded);
            if (!payload || typeof payload !== 'object')
                return null;
            return payload;
        }
        catch {
            return null;
        }
    }
    function getUcanTokenTiming(token, options = {}) {
        const nowMs = options.nowMs ?? Date.now();
        const payload = decodeUcanPayload(token);
        const exp = typeof payload?.exp === 'number' ? payload.exp : null;
        const nbf = typeof payload?.nbf === 'number' ? payload.nbf : null;
        const payloadWithIat = payload;
        const issuedAt = typeof payloadWithIat?.iat === 'number'
            ? payloadWithIat.iat
            : null;
        const remainingMs = exp === null ? null : exp - nowMs;
        const activeInMs = nbf === null ? 0 : Math.max(0, nbf - nowMs);
        const expired = exp === null || (remainingMs !== null && remainingMs <= 0);
        const notBefore = activeInMs > 0;
        return {
            valid: Boolean(payload && !expired && !notBefore),
            payload,
            exp,
            nbf,
            issuedAt,
            nowMs,
            remainingMs,
            activeInMs,
            expired,
            notBefore,
        };
    }
    function isUcanTokenFresh(tokenOrTiming, options = {}) {
        const timing = typeof tokenOrTiming === 'string'
            ? getUcanTokenTiming(tokenOrTiming, { nowMs: options.nowMs })
            : tokenOrTiming;
        if (!timing.valid)
            return false;
        const skewMs = Math.max(0, options.skewMs ?? DEFAULT_UCAN_TOKEN_SKEW_MS);
        return typeof timing.remainingMs === 'number' && timing.remainingMs > skewMs;
    }
    function readErrorField(error, field) {
        if (!error || typeof error !== 'object')
            return undefined;
        const value = error[field];
        if (value !== undefined)
            return value;
        const nestedError = error.error;
        if (nestedError && typeof nestedError === 'object') {
            return nestedError[field];
        }
        return undefined;
    }
    function classifyUcanAuthError(error) {
        const messageValue = readErrorField(error, 'message');
        const codeValue = readErrorField(error, 'code');
        const statusValue = readErrorField(error, 'status') ?? readErrorField(error, 'statusCode');
        const message = typeof messageValue === 'string'
            ? messageValue
            : error instanceof Error
                ? error.message
                : String(messageValue || error || '');
        const status = typeof statusValue === 'number' ? statusValue : undefined;
        const code = typeof codeValue === 'string' || typeof codeValue === 'number' ? codeValue : undefined;
        const normalized = `${message} ${String(code || '')}`.toLowerCase();
        if (/ucan.*expired|expired.*ucan|token.*expired|jwt.*expired|\bexp\b/.test(normalized)) {
            return { type: 'expired', message, retryable: true, shouldRefresh: true, status, code };
        }
        if (/not.?before|\bnbf\b|not yet valid/.test(normalized)) {
            return { type: 'not-before', message, retryable: true, shouldRefresh: false, status, code };
        }
        if (/invalid.*token|malformed.*token|bad.*ucan|invalid.*ucan/.test(normalized)) {
            return { type: 'invalid-token', message, retryable: true, shouldRefresh: true, status, code };
        }
        if (status === 401 || /unauthori[sz]ed|unauthenticated/.test(normalized)) {
            return { type: 'unauthorized', message, retryable: true, shouldRefresh: true, status, code };
        }
        if (status === 403 || /forbidden|permission denied|capability/.test(normalized)) {
            return { type: 'forbidden', message, retryable: false, shouldRefresh: false, status, code };
        }
        return { type: 'unknown', message, retryable: false, shouldRefresh: false, status, code };
    }
    function isReplayableRequestBody(body) {
        if (body == null)
            return true;
        if (typeof body === 'string')
            return true;
        if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams)
            return true;
        if (typeof FormData !== 'undefined' && body instanceof FormData)
            return true;
        if (typeof Blob !== 'undefined' && body instanceof Blob)
            return true;
        if (body instanceof ArrayBuffer)
            return true;
        if (ArrayBuffer.isView(body))
            return true;
        return false;
    }
    async function parseResponseJsonBody(response) {
        const text = await response.text();
        if (!text)
            return null;
        try {
            return JSON.parse(text);
        }
        catch {
            return { raw: text };
        }
    }
    function shouldRetryUcanFetch(response, errorInfo) {
        if (errorInfo.type === 'expired' || errorInfo.shouldRefresh) {
            return true;
        }
        if (response.status === 401)
            return true;
        if (response.status === 403 && errorInfo.type === 'forbidden')
            return false;
        return false;
    }
    function isSessionExpired(expiresAt, nowMs = Date.now()) {
        return typeof expiresAt === 'number' && nowMs >= expiresAt;
    }
    function encodeBase58(bytes) {
        if (bytes.length === 0)
            return '';
        let value = 0n;
        for (const byte of bytes) {
            value = (value << 8n) + BigInt(byte);
        }
        let encoded = '';
        while (value > 0n) {
            const mod = Number(value % 58n);
            encoded = `${BASE58_ALPHABET[mod]}${encoded}`;
            value /= 58n;
        }
        let leadingZeroCount = 0;
        while (leadingZeroCount < bytes.length && bytes[leadingZeroCount] === 0) {
            leadingZeroCount += 1;
        }
        if (leadingZeroCount > 0) {
            encoded = `${'1'.repeat(leadingZeroCount)}${encoded}`;
        }
        return encoded || '1';
    }
    function ensureWebCrypto() {
        if (typeof crypto === 'undefined' || !crypto.subtle) {
            throw new Error('WebCrypto not available for UCAN session');
        }
        return crypto;
    }
    function parseSessionId(options) {
        return options.id || DEFAULT_SESSION_ID;
    }
    function isLocalSessionRecord(record) {
        return Boolean(record?.source === 'local' || record?.privateKeyJwk);
    }
    async function buildDidKey(publicKey) {
        const webCrypto = ensureWebCrypto();
        const raw = new Uint8Array(await webCrypto.subtle.exportKey('raw', publicKey));
        const prefixed = new Uint8Array(DID_KEY_ED25519_MULTICODEC.length + raw.length);
        prefixed.set(DID_KEY_ED25519_MULTICODEC, 0);
        prefixed.set(raw, DID_KEY_ED25519_MULTICODEC.length);
        return `did:key:z${encodeBase58(prefixed)}`;
    }
    async function importLocalPrivateKey(privateKeyJwk) {
        const webCrypto = ensureWebCrypto();
        return await webCrypto.subtle.importKey('jwk', privateKeyJwk, 'Ed25519', true, ['sign']);
    }
    async function loadLocalSessionFromRecord(id, record) {
        if (!record || !isLocalSessionRecord(record) || !record.privateKeyJwk) {
            return null;
        }
        if (isSessionExpired(record.expiresAt)) {
            await deleteSessionRecord(id);
            return null;
        }
        try {
            const privateKey = await importLocalPrivateKey(record.privateKeyJwk);
            return {
                id: record.id || id,
                did: record.did,
                createdAt: record.createdAt,
                expiresAt: record.expiresAt,
                source: 'local',
                privateKey,
            };
        }
        catch {
            return null;
        }
    }
    function shouldKeepRootForSession(root, did, nowMs) {
        if (!root)
            return false;
        if (root.aud && root.aud !== did)
            return false;
        if (isRootExpired(root, nowMs))
            return false;
        return true;
    }
    async function createLocalSession(options, record) {
        const webCrypto = ensureWebCrypto();
        const sessionId = parseSessionId(options);
        if (!options.forceNew) {
            const existing = await loadLocalSessionFromRecord(sessionId, record);
            if (existing)
                return existing;
        }
        const pair = (await webCrypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']));
        const [privateKeyJwk, publicKeyJwk, did] = await Promise.all([
            webCrypto.subtle.exportKey('jwk', pair.privateKey),
            webCrypto.subtle.exportKey('jwk', pair.publicKey),
            buildDidKey(pair.publicKey),
        ]);
        const createdAt = Date.now();
        const expiresAt = normalizeExpiry(undefined, options.expiresInMs ?? DEFAULT_UCAN_SESSION_TTL_MS);
        const root = shouldKeepRootForSession(record?.root, did, createdAt) ? record?.root : undefined;
        await writeSessionRecord({
            id: sessionId,
            did,
            createdAt,
            expiresAt,
            source: 'local',
            privateKeyJwk,
            publicKeyJwk,
            root,
        });
        return {
            id: sessionId,
            did,
            createdAt,
            expiresAt,
            source: 'local',
            privateKey: pair.privateKey,
        };
    }
    function openDb() {
        if (typeof indexedDB === 'undefined') {
            return Promise.reject(new Error('IndexedDB not available'));
        }
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, 1);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(DB_STORE)) {
                    db.createObjectStore(DB_STORE, { keyPath: 'id' });
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }
    async function readSessionRecord(id) {
        try {
            const db = await openDb();
            return await new Promise((resolve, reject) => {
                const tx = db.transaction(DB_STORE, 'readonly');
                const store = tx.objectStore(DB_STORE);
                const request = store.get(id);
                request.onsuccess = () => resolve(request.result || null);
                request.onerror = () => reject(request.error);
            });
        }
        catch {
            return null;
        }
    }
    async function writeSessionRecord(record) {
        try {
            const db = await openDb();
            await new Promise((resolve, reject) => {
                const tx = db.transaction(DB_STORE, 'readwrite');
                const store = tx.objectStore(DB_STORE);
                const request = store.put(record);
                request.onsuccess = () => resolve();
                request.onerror = () => reject(request.error);
            });
        }
        catch {
            // ignore storage failures
        }
    }
    async function deleteSessionRecord(id) {
        try {
            const db = await openDb();
            await new Promise((resolve, reject) => {
                const tx = db.transaction(DB_STORE, 'readwrite');
                const store = tx.objectStore(DB_STORE);
                const request = store.delete(id);
                request.onsuccess = () => resolve();
                request.onerror = () => reject(request.error);
            });
        }
        catch {
            // ignore storage failures
        }
    }
    async function getUcanSession(id = DEFAULT_SESSION_ID, provider) {
        const record = await readSessionRecord(id);
        const walletProvider = provider || (typeof window !== 'undefined'
            ? await getProvider({ preferYeYing: true })
            : null);
        if (walletProvider) {
            try {
                return await requestWalletUcanSession(walletProvider, { id });
            }
            catch {
                return await loadLocalSessionFromRecord(id, record);
            }
        }
        return await loadLocalSessionFromRecord(id, record);
    }
    async function requestWalletUcanSession(provider, options) {
        const sessionId = options.id || DEFAULT_SESSION_ID;
        const result = (await provider.request({
            method: 'yeying_ucan_session',
            params: [
                {
                    sessionId,
                    expiresInMs: options.expiresInMs,
                    forceNew: options.forceNew,
                },
            ],
        }));
        if (!result || typeof result.did !== 'string') {
            throw new Error('Invalid wallet UCAN session response');
        }
        const createdAt = typeof result.createdAt === 'number' ? result.createdAt : Date.now();
        const expiresAt = typeof result.expiresAt === 'number' ? result.expiresAt : null;
        const existing = await readSessionRecord(sessionId);
        const nextRecord = {
            id: result.id || sessionId,
            did: result.did,
            createdAt,
            expiresAt,
            source: 'wallet',
            root: existing?.root,
        };
        if (nextRecord.root && nextRecord.root.aud && nextRecord.root.aud !== nextRecord.did) {
            nextRecord.root = undefined;
        }
        await writeSessionRecord(nextRecord);
        return {
            id: result.id || sessionId,
            did: result.did,
            createdAt,
            expiresAt,
            source: 'wallet',
            signer: async (signingInput, payload) => {
                const signatureResult = (await provider.request({
                    method: 'yeying_ucan_sign',
                    params: [
                        {
                            sessionId,
                            signingInput,
                            payload,
                        },
                    ],
                }));
                if (typeof signatureResult === 'string') {
                    return signatureResult;
                }
                if (signatureResult && typeof signatureResult.signature === 'string') {
                    return signatureResult.signature;
                }
                throw new Error('Invalid wallet UCAN signature response');
            },
        };
    }
    async function createUcanSession(options = {}) {
        const sessionId = parseSessionId(options);
        const record = await readSessionRecord(sessionId);
        const provider = options.provider || (typeof window !== 'undefined'
            ? await getProvider({ preferYeYing: true })
            : null);
        if (provider) {
            try {
                return await requestWalletUcanSession(provider, { ...options, id: sessionId });
            }
            catch {
                // fallback to local ed25519 session
            }
        }
        return await createLocalSession({ ...options, id: sessionId }, record);
    }
    async function clearUcanSession(id = DEFAULT_SESSION_ID) {
        await deleteSessionRecord(id);
    }
    async function storeUcanRoot(root, id = DEFAULT_SESSION_ID) {
        const record = await readSessionRecord(id);
        const createdAt = record?.createdAt ?? Date.now();
        const expiresAt = record?.expiresAt ?? null;
        const did = record?.did || root.aud;
        const nextRecord = {
            ...(record || {}),
            id,
            did,
            createdAt,
            expiresAt,
            root,
        };
        await writeSessionRecord(nextRecord);
    }
    async function getStoredUcanRoot(id = DEFAULT_SESSION_ID) {
        const record = await readSessionRecord(id);
        return record?.root || null;
    }
    function capsEqual(a, b) {
        const left = normalizeUcanCapabilities(a, { includeLegacyAliases: false });
        const right = normalizeUcanCapabilities(b, { includeLegacyAliases: false });
        return JSON.stringify(left) === JSON.stringify(right);
    }
    function isRootExpired(root, nowMs) {
        return Boolean(root.exp && nowMs > root.exp);
    }
    async function getOrCreateUcanRoot(options) {
        const provider = options.provider || (await requireProvider());
        const session = options.session || (await createUcanSession({ id: options.sessionId, provider }));
        const nowMs = Date.now();
        const stored = await getStoredUcanRoot(session.id);
        if (stored &&
            (!stored.aud || stored.aud === session.did) &&
            capsEqual(stored.cap, options.capabilities) &&
            !isRootExpired(stored, nowMs)) {
            return stored;
        }
        return await createRootUcan({ ...options, provider, session });
    }
    function buildUcanStatement(payload) {
        return `UCAN-AUTH ${JSON.stringify(payload)}`;
    }
    function buildSiweMessage(params) {
        const lines = [
            `${params.domain} wants you to sign in with your Ethereum account:`,
            params.address,
            '',
            params.statement,
            '',
            `URI: ${params.uri}`,
            'Version: 1',
            `Chain ID: ${params.chainId}`,
            `Nonce: ${params.nonce}`,
            `Issued At: ${params.issuedAt}`,
        ];
        if (params.expirationTime) {
            lines.push(`Expiration Time: ${params.expirationTime}`);
        }
        return lines.join('\n');
    }
    async function resolveAddress(provider, address) {
        if (address)
            return address;
        let accounts = await getAccounts(provider);
        if (!accounts[0]) {
            const requested = (await provider.request({
                method: 'eth_requestAccounts',
            }));
            if (Array.isArray(requested)) {
                accounts = requested;
            }
        }
        if (!accounts[0])
            throw new Error('No account available');
        return accounts[0];
    }
    async function signWithProvider(provider, address, message) {
        const signature = await provider.request({
            method: 'personal_sign',
            params: [message, address],
        });
        if (typeof signature !== 'string') {
            throw new Error('Invalid signature response');
        }
        return signature;
    }
    async function createRootUcan(options) {
        const provider = options.provider || (await requireProvider());
        const session = options.session || (await createUcanSession({ id: options.sessionId, provider }));
        const address = await resolveAddress(provider, options.address);
        const chainId = options.chainId || (await getChainId(provider)) || '1';
        const domain = options.domain || (typeof window !== 'undefined' ? window.location.host : '127.0.0.1');
        const uri = options.uri || (typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1');
        const nonce = options.nonce || randomNonce(8);
        const exp = normalizeExpiry(undefined, options.expiresInMs ?? DEFAULT_UCAN_SESSION_TTL_MS);
        const nbf = options.notBeforeMs;
        const normalizedCapabilities = normalizeUcanCapabilities(options.capabilities);
        if (!normalizedCapabilities.length) {
            throw new Error('Missing UCAN capabilities');
        }
        const statementPayload = {
            aud: session.did,
            cap: normalizedCapabilities,
            exp,
        };
        if (nbf)
            statementPayload.nbf = nbf;
        const statement = options.statement || buildUcanStatement(statementPayload);
        const issuedAt = new Date().toISOString();
        const expirationTime = new Date(exp).toISOString();
        const message = buildSiweMessage({
            domain,
            address,
            statement,
            uri,
            chainId,
            nonce,
            issuedAt,
            expirationTime,
        });
        const signature = await signWithProvider(provider, address, message);
        const root = {
            type: 'siwe',
            iss: `did:pkh:eth:${address.toLowerCase()}`,
            aud: session.did,
            cap: normalizedCapabilities,
            exp,
            nbf,
            siwe: {
                message,
                signature,
            },
        };
        await storeUcanRoot(root, session.id);
        return root;
    }
    async function signUcanPayload(payload, session) {
        const header = { alg: 'EdDSA', typ: 'UCAN' };
        const headerB64 = encodeJson(header);
        const payloadB64 = encodeJson(payload);
        const signingInput = `${headerB64}.${payloadB64}`;
        let signatureB64;
        if (session.signer) {
            signatureB64 = await session.signer(signingInput, payload);
        }
        else {
            if (!session.privateKey) {
                throw new Error('Missing UCAN session key');
            }
            const data = textEncoder.encode(signingInput);
            const signature = await crypto.subtle.sign('Ed25519', session.privateKey, data);
            signatureB64 = toBase64Url(signature);
        }
        return `${headerB64}.${payloadB64}.${signatureB64}`;
    }
    async function resolveProofs(options, issuer) {
        if (options.proofs && options.proofs.length > 0)
            return options.proofs;
        const stored = await getStoredUcanRoot(options.sessionId || DEFAULT_SESSION_ID);
        if (!stored) {
            throw new Error('Missing UCAN proof chain');
        }
        if (issuer?.did && stored.aud && stored.aud !== issuer.did) {
            throw new Error('UCAN root audience mismatch');
        }
        return [stored];
    }
    async function createDelegationUcan(options) {
        const issuer = options.issuer || (await createUcanSession({
            id: options.sessionId,
            provider: options.provider,
        }));
        if (!issuer)
            throw new Error('Missing UCAN session key');
        const normalizedCapabilities = normalizeUcanCapabilities(options.capabilities);
        if (!normalizedCapabilities.length) {
            throw new Error('Missing UCAN capabilities');
        }
        const exp = normalizeExpiry(undefined, options.expiresInMs ?? DEFAULT_UCAN_TOKEN_TTL_MS);
        const payload = {
            iss: issuer.did,
            aud: options.audience,
            cap: normalizedCapabilities,
            exp,
            nbf: options.notBeforeMs,
            prf: await resolveProofs(options, issuer),
        };
        return await signUcanPayload(payload, issuer);
    }
    async function createInvocationUcan(options) {
        const issuer = options.issuer || (await createUcanSession({
            id: options.sessionId,
            provider: options.provider,
        }));
        if (!issuer)
            throw new Error('Missing UCAN session key');
        const normalizedCapabilities = normalizeUcanCapabilities(options.capabilities);
        if (!normalizedCapabilities.length) {
            throw new Error('Missing UCAN capabilities');
        }
        const exp = normalizeExpiry(undefined, options.expiresInMs ?? DEFAULT_UCAN_TOKEN_TTL_MS);
        const payload = {
            iss: issuer.did,
            aud: options.audience,
            cap: normalizedCapabilities,
            exp,
            nbf: options.notBeforeMs,
            prf: await resolveProofs(options, issuer),
        };
        return await signUcanPayload(payload, issuer);
    }
    async function getOrCreateInvocationUcan(options) {
        if (options.ucan &&
            isUcanTokenFresh(options.ucan, {
                nowMs: options.nowMs,
                skewMs: options.skewMs,
            })) {
            return options.ucan;
        }
        return await createInvocationUcan({
            issuer: options.issuer,
            sessionId: options.sessionId,
            provider: options.provider,
            audience: options.audience,
            capabilities: options.capabilities,
            expiresInMs: options.expiresInMs,
            notBeforeMs: options.notBeforeMs,
            proofs: options.proofs,
        });
    }
    async function authUcanFetch(input, init = {}, options = {}) {
        const fetcher = options.fetcher || fetch;
        const audience = options.audience;
        const capabilities = options.capabilities;
        const canRefresh = Boolean(audience && capabilities);
        const canRetry = canRefresh && isReplayableRequestBody(init.body);
        let token = options.ucan || '';
        if (!token || (canRefresh && !isUcanTokenFresh(token, { skewMs: options.skewMs }))) {
            if (!audience || !capabilities) {
                throw new Error('Missing UCAN audience or capabilities');
            }
            token = await getOrCreateInvocationUcan({
                ucan: options.ucan,
                issuer: options.issuer,
                sessionId: options.sessionId,
                provider: options.provider,
                audience,
                capabilities,
                expiresInMs: options.expiresInMs,
                notBeforeMs: options.notBeforeMs,
                proofs: options.proofs,
                skewMs: options.skewMs,
            });
        }
        const makeHeaders = (bearer) => {
            const headers = new Headers(init.headers || {});
            headers.set('Authorization', `Bearer ${bearer}`);
            return headers;
        };
        let response = await fetcher(input, {
            ...init,
            headers: makeHeaders(token),
        });
        if (!canRetry || response.ok) {
            return response;
        }
        const payload = await parseResponseJsonBody(response.clone()).catch(() => null);
        const errorInfo = classifyUcanAuthError(payload || response.statusText || response);
        if (!shouldRetryUcanFetch(response, errorInfo)) {
            return response;
        }
        if (!audience || !capabilities) {
            return response;
        }
        const refreshedToken = await createInvocationUcan({
            issuer: options.issuer,
            sessionId: options.sessionId,
            provider: options.provider,
            audience,
            capabilities,
            expiresInMs: options.expiresInMs,
            notBeforeMs: options.notBeforeMs,
            proofs: options.proofs,
        });
        response = await fetcher(input, {
            ...init,
            headers: makeHeaders(refreshedToken),
        });
        return response;
    }

    const DEFAULT_BASE_URL = '/api/v1/public/auth/central';
    const DEFAULT_ISSUER_PATH = 'issuer';
    const DEFAULT_SESSION_PATH = 'session';
    const DEFAULT_ISSUE_PATH = 'issue';
    const DEFAULT_SESSION_TOKEN_KEY = 'centralUcanSessionToken';
    let cachedCentralSessionToken = null;
    function normalizeBaseUrl$1(baseUrl) {
        return baseUrl.replace(/\/+$/, '');
    }
    function joinUrl$1(baseUrl, path) {
        const trimmed = path.replace(/^\/+/, '');
        return `${normalizeBaseUrl$1(baseUrl)}/${trimmed}`;
    }
    function resolveBaseUrl(options) {
        return options?.baseUrl || DEFAULT_BASE_URL;
    }
    function resolveFetcher(options) {
        return options?.fetcher || fetch;
    }
    function resolveCredentials(options) {
        return options?.credentials ?? 'include';
    }
    function resolveSessionTokenKey(options) {
        return options?.sessionTokenStorageKey || DEFAULT_SESSION_TOKEN_KEY;
    }
    function resolveAccessToken(options) {
        if (typeof options?.accessToken === 'string') {
            const token = options.accessToken.trim();
            return token || null;
        }
        if (options?.accessToken === null) {
            return null;
        }
        return getAccessToken(options);
    }
    function shouldStoreSessionToken(options) {
        return options?.storeSessionToken !== false;
    }
    function parseObject(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) {
            return {};
        }
        return value;
    }
    function parseEnvelopeData(payload) {
        const root = parseObject(payload);
        if (Object.prototype.hasOwnProperty.call(root, 'data')) {
            return parseObject(root.data);
        }
        return root;
    }
    function parseStringField(obj, keys) {
        for (const key of keys) {
            const value = obj[key];
            if (typeof value === 'string') {
                return value;
            }
        }
        return undefined;
    }
    function parseNumberField(obj, keys) {
        for (const key of keys) {
            const value = obj[key];
            if (typeof value === 'number' && Number.isFinite(value)) {
                return value;
            }
        }
        return undefined;
    }
    function parseCapabilitiesField(obj, keys) {
        for (const key of keys) {
            const value = obj[key];
            if (!Array.isArray(value))
                continue;
            const caps = value
                .filter(item => item && typeof item === 'object')
                .map(item => normalizeUcanCapability(item))
                .filter((cap) => Boolean(cap));
            return caps;
        }
        return undefined;
    }
    function parseIssuerDidField(obj) {
        return parseStringField(obj, ['issuerDid', 'issuer', 'did']);
    }
    function readStoredSessionToken(options) {
        if (!shouldStoreSessionToken(options))
            return null;
        if (typeof localStorage === 'undefined')
            return null;
        const key = resolveSessionTokenKey(options);
        return localStorage.getItem(key);
    }
    function persistSessionToken(token, options) {
        cachedCentralSessionToken = token;
        if (!shouldStoreSessionToken(options))
            return;
        if (typeof localStorage === 'undefined')
            return;
        const key = resolveSessionTokenKey(options);
        if (!token) {
            localStorage.removeItem(key);
        }
        else {
            localStorage.setItem(key, token);
        }
    }
    async function parseJsonBody(response) {
        const text = await response.text();
        if (!text)
            return null;
        try {
            return JSON.parse(text);
        }
        catch {
            return { raw: text };
        }
    }
    function getCentralSessionToken(options) {
        if (cachedCentralSessionToken)
            return cachedCentralSessionToken;
        const stored = readStoredSessionToken(options);
        if (stored) {
            cachedCentralSessionToken = stored;
        }
        return stored;
    }
    function setCentralSessionToken(token, options) {
        persistSessionToken(token, options);
    }
    function clearCentralSessionToken(options) {
        cachedCentralSessionToken = null;
        if (typeof localStorage === 'undefined')
            return;
        const key = resolveSessionTokenKey(options);
        localStorage.removeItem(key);
    }
    async function getCentralIssuerInfo(options = {}) {
        const fetcher = resolveFetcher(options);
        const credentials = resolveCredentials(options);
        const url = joinUrl$1(resolveBaseUrl(options), options.issuerPath || DEFAULT_ISSUER_PATH);
        const token = resolveAccessToken(options);
        const headers = new Headers({
            accept: 'application/json',
        });
        if (token) {
            headers.set('Authorization', `Bearer ${token}`);
        }
        const response = await fetcher(url, {
            method: 'GET',
            headers,
            credentials,
        });
        const payload = await parseJsonBody(response);
        if (!response.ok) {
            throw new Error(`Central issuer request failed: ${response.status} ${JSON.stringify(payload)}`);
        }
        const data = parseEnvelopeData(payload);
        return {
            enabled: typeof data.enabled === 'boolean' ? data.enabled : undefined,
            issuerDid: parseIssuerDidField(data),
            defaultAudience: parseStringField(data, ['defaultAudience']),
            defaultCapabilities: parseCapabilitiesField(data, ['defaultCapabilities']),
            response: payload,
        };
    }
    async function createCentralSession(options) {
        const subject = String(options?.subject || '').trim();
        if (!subject) {
            throw new Error('Missing subject');
        }
        const fetcher = resolveFetcher(options);
        const credentials = resolveCredentials(options);
        const accessToken = resolveAccessToken(options);
        const url = joinUrl$1(resolveBaseUrl(options), options.sessionPath || DEFAULT_SESSION_PATH);
        const headers = new Headers({
            'Content-Type': 'application/json',
            accept: 'application/json',
        });
        if (accessToken) {
            headers.set('Authorization', `Bearer ${accessToken}`);
        }
        const response = await fetcher(url, {
            method: 'POST',
            headers,
            credentials,
            body: JSON.stringify({
                subject,
                sessionTtlMs: options.sessionTtlMs,
            }),
        });
        const payload = await parseJsonBody(response);
        if (!response.ok) {
            throw new Error(`Central session request failed: ${response.status} ${JSON.stringify(payload)}`);
        }
        const data = parseEnvelopeData(payload);
        const sessionToken = parseStringField(data, ['sessionToken']);
        if (!sessionToken) {
            throw new Error('Central session response missing sessionToken');
        }
        persistSessionToken(sessionToken, options);
        return {
            subject: parseStringField(data, ['subject']) || subject,
            sessionToken,
            expiresAt: parseNumberField(data, ['expiresAt']),
            issuerDid: parseIssuerDidField(data),
            response: payload,
        };
    }
    async function issueCentralUcan(options = {}) {
        const sessionToken = options.sessionToken || getCentralSessionToken(options);
        if (!sessionToken) {
            throw new Error('Missing central session token');
        }
        const normalizedCapabilities = options.capabilities
            ? normalizeUcanCapabilities(options.capabilities)
            : undefined;
        const fetcher = resolveFetcher(options);
        const credentials = resolveCredentials(options);
        const url = joinUrl$1(resolveBaseUrl(options), options.issuePath || DEFAULT_ISSUE_PATH);
        const response = await fetcher(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                accept: 'application/json',
                Authorization: `Bearer ${sessionToken}`,
            },
            credentials,
            body: JSON.stringify({
                audience: options.audience,
                capabilities: normalizedCapabilities,
                expiresInMs: options.expiresInMs,
                ttlMs: options.ttlMs,
            }),
        });
        const payload = await parseJsonBody(response);
        if (!response.ok) {
            throw new Error(`Central UCAN issue failed: ${response.status} ${JSON.stringify(payload)}`);
        }
        const data = parseEnvelopeData(payload);
        const ucan = parseStringField(data, ['ucan']);
        if (!ucan) {
            throw new Error('Central UCAN response missing ucan');
        }
        return {
            ucan,
            issuerDid: parseIssuerDidField(data),
            subject: parseStringField(data, ['subject']),
            audience: parseStringField(data, ['audience']),
            capabilities: parseCapabilitiesField(data, ['capabilities']),
            exp: parseNumberField(data, ['exp', 'expiresAt']),
            nbf: parseNumberField(data, ['nbf', 'notBefore']),
            iat: parseNumberField(data, ['iat', 'issuedAt']),
            response: payload,
        };
    }
    async function createAndIssueCentralUcan(options) {
        const session = await createCentralSession({
            ...options,
            subject: options.subject,
            sessionTtlMs: options.sessionTtlMs,
        });
        const issue = await issueCentralUcan({
            ...options,
            sessionToken: session.sessionToken,
            audience: options.audience,
            capabilities: options.capabilities,
            expiresInMs: options.expiresInMs,
            ttlMs: options.ttlMs,
        });
        return { session, issue };
    }
    async function authCentralUcanFetch(input, init = {}, options = {}) {
        const fetcher = resolveFetcher(options);
        const credentials = resolveCredentials(options);
        let token = options.ucan || null;
        if (!token) {
            let sessionToken = options.sessionToken || getCentralSessionToken(options);
            if (!sessionToken) {
                if (!options.subject) {
                    throw new Error('Missing central session token or subject');
                }
                const session = await createCentralSession({
                    ...options,
                    subject: options.subject,
                    sessionTtlMs: options.sessionTtlMs,
                });
                sessionToken = session.sessionToken;
            }
            const issued = await issueCentralUcan({
                ...options,
                sessionToken,
                audience: options.audience,
                capabilities: options.capabilities,
                expiresInMs: options.expiresInMs,
                ttlMs: options.ttlMs,
            });
            token = issued.ucan;
        }
        const headers = new Headers(init.headers || {});
        headers.set('Authorization', `Bearer ${token}`);
        return fetcher(input, {
            ...init,
            headers,
            credentials,
        });
    }

    function normalizeBaseUrl(baseUrl) {
        return baseUrl.replace(/\/+$/, '');
    }
    function normalizePrefix(prefix) {
        if (!prefix || prefix === '/')
            return '';
        let next = prefix.startsWith('/') ? prefix : `/${prefix}`;
        next = next.replace(/\/+$/, '');
        return next;
    }
    function normalizePath(path) {
        if (!path || path === '/')
            return '/';
        const next = path.startsWith('/') ? path : `/${path}`;
        return encodeURI(next);
    }
    function joinUrl(baseUrl, path) {
        const base = normalizeBaseUrl(baseUrl);
        const suffix = path.startsWith('/') ? path : `/${path}`;
        return `${base}${suffix}`;
    }
    function isRecord(value) {
        return typeof value === 'object' && value !== null;
    }
    function resolveAuthHeader(auth, token) {
        if (auth?.type === 'bearer') {
            return `Bearer ${auth.token}`;
        }
        if (auth?.type === 'basic') {
            const raw = `${auth.username}:${auth.password}`;
            return `Basic ${btoa(raw)}`;
        }
        if (token) {
            return `Bearer ${token}`;
        }
        return null;
    }
    class WebDavClient {
        baseUrl;
        prefix;
        auth;
        token;
        fetcher;
        credentials;
        constructor(options) {
            this.baseUrl = normalizeBaseUrl(options.baseUrl);
            this.prefix = normalizePrefix(options.prefix);
            this.auth = options.auth;
            this.token = options.token;
            this.fetcher = options.fetcher || ((input, init) => fetch(input, init));
            this.credentials = options.credentials;
        }
        setToken(token) {
            this.token = token || undefined;
        }
        setAuth(auth) {
            this.auth = auth;
        }
        buildUrl(path) {
            const webdavPath = `${this.prefix}${normalizePath(path)}`;
            return `${this.baseUrl}${webdavPath}`;
        }
        buildHeaders(options) {
            const headers = new Headers(options?.headers || {});
            const authHeader = resolveAuthHeader(options?.auth || this.auth, options?.token || this.token);
            if (authHeader) {
                headers.set('Authorization', authHeader);
            }
            if (options?.depth !== undefined) {
                headers.set('Depth', String(options.depth));
            }
            if (typeof options?.overwrite === 'boolean') {
                headers.set('Overwrite', options.overwrite ? 'T' : 'F');
            }
            if (options?.contentType) {
                headers.set('Content-Type', options.contentType);
            }
            return headers;
        }
        async request(method, path, body, options = {}) {
            const response = await this.fetcher(this.buildUrl(path), {
                method,
                headers: this.buildHeaders(options),
                body: body ?? undefined,
                credentials: this.credentials,
                signal: options.signal,
            });
            if (!response.ok) {
                throw new Error(`WebDAV ${method} ${path} failed: ${response.status} ${response.statusText}`);
            }
            return response;
        }
        async listDirectory(path = '/', depth = 1) {
            const res = await this.request('PROPFIND', path, null, { depth });
            return await res.text();
        }
        async download(path) {
            return await this.request('GET', path);
        }
        async downloadText(path) {
            const res = await this.download(path);
            return await res.text();
        }
        async downloadArrayBuffer(path) {
            const res = await this.download(path);
            return await res.arrayBuffer();
        }
        async upload(path, content, contentType) {
            return await this.request('PUT', path, content, { contentType });
        }
        async createDirectory(path) {
            return await this.request('MKCOL', path);
        }
        async ensureDirectory(path) {
            if (!path || path === '/')
                return;
            const segments = path.split('/').filter(Boolean);
            if (segments.length === 0)
                return;
            let current = '';
            for (const segment of segments) {
                current = `${current}/${segment}`;
                const res = await this.fetcher(this.buildUrl(current), {
                    method: 'MKCOL',
                    headers: this.buildHeaders(),
                    credentials: this.credentials,
                });
                if (res.ok)
                    continue;
                if (res.status === 405)
                    continue;
                throw new Error(`WebDAV MKCOL ${current} failed: ${res.status} ${res.statusText}`);
            }
        }
        async remove(path) {
            return await this.request('DELETE', path);
        }
        async move(path, destination, overwrite = true) {
            const destinationUrl = destination.startsWith('http')
                ? destination
                : this.buildUrl(destination);
            return await this.request('MOVE', path, null, {
                headers: { Destination: destinationUrl },
                overwrite,
            });
        }
        async copy(path, destination, overwrite = true) {
            const destinationUrl = destination.startsWith('http')
                ? destination
                : this.buildUrl(destination);
            return await this.request('COPY', path, null, {
                headers: { Destination: destinationUrl },
                overwrite,
            });
        }
        async getQuota() {
            const res = await this.fetcher(joinUrl(this.baseUrl, '/api/v1/public/webdav/quota'), {
                method: 'GET',
                headers: this.buildHeaders(),
                credentials: this.credentials,
            });
            if (!res.ok) {
                throw new Error(`WebDAV quota failed: ${res.status} ${res.statusText}`);
            }
            return await res.json();
        }
        async listRecycle() {
            const res = await this.fetcher(joinUrl(this.baseUrl, '/api/v1/public/webdav/recycle/list'), {
                method: 'GET',
                headers: this.buildHeaders(),
                credentials: this.credentials,
            });
            if (!res.ok) {
                throw new Error(`WebDAV recycle list failed: ${res.status} ${res.statusText}`);
            }
            return await res.json();
        }
        async recoverRecycle(hash) {
            const res = await this.fetcher(joinUrl(this.baseUrl, '/api/v1/public/webdav/recycle/recover'), {
                method: 'POST',
                headers: this.buildHeaders({ contentType: 'application/json' }),
                body: JSON.stringify({ hash }),
                credentials: this.credentials,
            });
            if (!res.ok) {
                throw new Error(`WebDAV recycle recover failed: ${res.status} ${res.statusText}`);
            }
            return await res.json();
        }
        async deleteRecycle(hash) {
            const res = await this.fetcher(joinUrl(this.baseUrl, '/api/v1/public/webdav/recycle/permanent'), {
                method: 'DELETE',
                headers: this.buildHeaders({ contentType: 'application/json' }),
                body: JSON.stringify({ hash }),
                credentials: this.credentials,
            });
            if (!res.ok) {
                throw new Error(`WebDAV recycle delete failed: ${res.status} ${res.statusText}`);
            }
            return await res.json();
        }
        async clearRecycle() {
            const res = await this.fetcher(joinUrl(this.baseUrl, '/api/v1/public/webdav/recycle/clear'), {
                method: 'DELETE',
                headers: this.buildHeaders(),
                credentials: this.credentials,
            });
            if (!res.ok) {
                throw new Error(`WebDAV recycle clear failed: ${res.status} ${res.statusText}`);
            }
            return await res.json();
        }
        async requestApiJson(method, apiPath, body, options) {
            const headers = this.buildHeaders({
                auth: options?.auth,
                token: options?.token,
                contentType: body === undefined ? undefined : 'application/json',
            });
            const response = await this.fetcher(joinUrl(this.baseUrl, apiPath), {
                method,
                headers,
                body: body === undefined ? undefined : JSON.stringify(body),
                credentials: this.credentials,
                signal: options?.signal,
            });
            if (!response.ok) {
                throw new Error(`WebDAV ${method} ${apiPath} failed: ${response.status} ${response.statusText}`);
            }
            return await response.json();
        }
        getShareAccessUrl(token, fileName) {
            const normalizedToken = encodeURIComponent(String(token || '').trim());
            if (!normalizedToken) {
                throw new Error('Share token is required');
            }
            const encodedFileName = String(fileName || '').trim()
                ? `/${encodeURIComponent(String(fileName || '').trim())}`
                : '';
            return joinUrl(this.baseUrl, `/api/v1/public/share/${normalizedToken}${encodedFileName}`);
        }
        async createShareLink(options) {
            const normalizedPath = String(options.path || '').trim();
            if (!normalizedPath) {
                throw new Error('Share path is required');
            }
            const payload = await this.requestApiJson('POST', '/api/v1/public/share/create', {
                path: normalizedPath,
                expiresIn: options.expiresIn,
                expiresValue: options.expiresValue,
                expiresUnit: options.expiresUnit,
            }, options);
            if (!isRecord(payload)) {
                throw new Error('WebDAV share create response is invalid');
            }
            return payload;
        }
        async listShareLinks(options = {}) {
            const payload = await this.requestApiJson('GET', '/api/v1/public/share/list', undefined, options);
            if (!isRecord(payload)) {
                return [];
            }
            const items = payload.items;
            if (!Array.isArray(items)) {
                return [];
            }
            return items.filter(isRecord);
        }
        async revokeShareLink(token, options = {}) {
            const normalizedToken = String(token || '').trim();
            if (!normalizedToken) {
                throw new Error('Share token is required');
            }
            const payload = await this.requestApiJson('POST', '/api/v1/public/share/revoke', { token: normalizedToken }, options);
            if (!isRecord(payload)) {
                return {};
            }
            return payload;
        }
    }
    function createWebDavClient(options) {
        return new WebDavClient(options);
    }

    const tokenCache = new Map();
    const DEFAULT_APP_ACTION = 'write';
    const LOOPBACK_HOST_ALIASES = new Set([
        'localhost',
        '127.0.0.1',
        '::1',
        '0:0:0:0:0:0:0:1',
        '0.0.0.0',
    ]);
    function normalizeAppDir(path) {
        const trimmed = path.trim();
        if (!trimmed)
            return '/';
        let next = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
        next = next.replace(/\/+$/, '');
        return next || '/';
    }
    function sanitizeAppId(appId) {
        return appId.trim().replace(/[^a-zA-Z0-9._-]/g, '-');
    }
    function parseHostPort(rawHost) {
        const host = rawHost.trim();
        if (!host)
            return { hostname: '', port: '' };
        const bracketMatch = host.match(/^\[([^\]]+)\](?::([0-9]+))?$/);
        if (bracketMatch) {
            return {
                hostname: bracketMatch[1] || '',
                port: bracketMatch[2] || '',
            };
        }
        const firstColon = host.indexOf(':');
        const lastColon = host.lastIndexOf(':');
        if (firstColon > -1 && firstColon === lastColon) {
            const hostname = host.slice(0, firstColon).trim();
            const port = host.slice(firstColon + 1).trim();
            if (/^[0-9]+$/.test(port)) {
                return { hostname, port };
            }
        }
        return { hostname: host, port: '' };
    }
    function normalizeAppHostnameForAppId(hostname) {
        const normalized = (hostname || '').trim().toLowerCase();
        if (!normalized)
            return '';
        const bare = normalized.replace(/^\[(.*)\]$/, '$1');
        if (LOOPBACK_HOST_ALIASES.has(normalized) || LOOPBACK_HOST_ALIASES.has(bare)) {
            return 'localhost';
        }
        return bare;
    }
    function buildSanitizedAppId(hostname, port) {
        const normalizedHostname = normalizeAppHostnameForAppId(hostname);
        if (!normalizedHostname)
            return '';
        const normalizedPort = port === undefined || port === null ? '' : String(port).trim();
        const host = normalizedPort
            ? `${normalizedHostname}:${normalizedPort}`
            : normalizedHostname;
        return sanitizeAppId(host);
    }
    function deriveAppIdFromHost(host) {
        const parsed = parseHostPort(host || '');
        return buildSanitizedAppId(parsed.hostname, parsed.port);
    }
    function deriveAppIdFromLocation(locationLike) {
        const source = locationLike ||
            (typeof window !== 'undefined' ? window.location : undefined);
        if (!source)
            return '';
        const hostname = typeof source.hostname === 'string' ? source.hostname : '';
        const port = source.port;
        if (hostname) {
            const appId = buildSanitizedAppId(hostname, port);
            if (appId)
                return appId;
        }
        if (typeof source.host === 'string') {
            return deriveAppIdFromHost(source.host);
        }
        return '';
    }
    function normalizeAction(action) {
        const trimmed = (action || '').trim();
        return trimmed ? trimmed : null;
    }
    function buildAppCapability(options) {
        if (!options.appId)
            return null;
        const action = normalizeAction(options.appAction) || DEFAULT_APP_ACTION;
        const resource = `app:all:${sanitizeAppId(options.appId)}`;
        return {
            with: resource,
            can: action,
            resource,
            action,
        };
    }
    function hasAppCapability(caps) {
        return (caps || []).some(cap => getCapabilityResource(cap).startsWith('app:'));
    }
    function dedupeCapabilities(caps) {
        return normalizeUcanCapabilities(caps);
    }
    function ensureAppCapability(caps, options) {
        const appCap = buildAppCapability(options);
        if (!appCap)
            return caps || [];
        if (hasAppCapability(caps || []))
            return caps || [];
        return dedupeCapabilities([...(caps || []), appCap]);
    }
    function resolveAppDir(options) {
        if (options.appDir) {
            return normalizeAppDir(options.appDir);
        }
        if (options.appId) {
            return normalizeAppDir(`/apps/${sanitizeAppId(options.appId)}`);
        }
        return undefined;
    }
    function buildCapsKey(caps) {
        const canonical = (caps || [])
            .map(cap => ({
            with: getCapabilityResource(cap),
            can: getCapabilityAction(cap),
        }))
            .filter(cap => Boolean(cap.with && cap.can));
        return JSON.stringify(canonical);
    }
    function buildTokenCacheKey(issuer, audience, caps) {
        return `${issuer.did}|${audience}|${buildCapsKey(caps)}`;
    }
    function resolveWebdavCaps(options) {
        const baseCaps = options.capabilities || options.root?.cap || [];
        return ensureAppCapability(baseCaps, options);
    }
    function resolveInvocationCaps(options, fallbackCaps) {
        const caps = options.invocationCapabilities || fallbackCaps;
        return ensureAppCapability(caps, options);
    }
    async function getCachedInvocationToken(options) {
        const cacheKey = buildTokenCacheKey(options.issuer, options.audience, options.capabilities);
        const cached = tokenCache.get(cacheKey);
        const nowMs = Date.now();
        const token = await getOrCreateInvocationUcan({
            ucan: cached?.token,
            issuer: options.issuer,
            audience: options.audience,
            capabilities: options.capabilities,
            proofs: options.proofs,
            expiresInMs: options.expiresInMs,
            skewMs: options.skewMs,
            nowMs,
            notBeforeMs: options.notBeforeMs,
        });
        const payload = decodeUcanPayload(token);
        if (payload && typeof payload.exp === 'number') {
            tokenCache.set(cacheKey, {
                token,
                exp: payload.exp,
                nbf: payload.nbf,
            });
        }
        return token;
    }
    async function initWebDavStorage(options) {
        const caps = resolveWebdavCaps(options);
        if (!caps || caps.length === 0) {
            throw new Error('Missing UCAN capabilities for WebDAV');
        }
        const needsProvider = !options.session || !options.root;
        const provider = options.provider || (needsProvider ? await requireProvider() : undefined);
        const session = options.session ||
            (await createUcanSession({
                id: options.sessionId,
                provider,
            }));
        const nowMs = Date.now();
        let root = options.root;
        if (root && root.aud && root.aud !== session.did) {
            root = undefined;
        }
        if (root && buildCapsKey(root.cap) !== buildCapsKey(caps)) {
            root = undefined;
        }
        if (root && root.exp && nowMs > root.exp) {
            root = undefined;
        }
        if (!root) {
            root = await getOrCreateUcanRoot({
                provider: provider || (await requireProvider()),
                session,
                capabilities: caps,
                expiresInMs: options.rootExpiresInMs,
            });
        }
        const invocationCaps = resolveInvocationCaps(options, caps);
        const token = await getCachedInvocationToken({
            issuer: session,
            audience: options.audience,
            capabilities: invocationCaps,
            proofs: [root],
            expiresInMs: options.invocationExpiresInMs,
            skewMs: options.invocationSkewMs,
            notBeforeMs: options.notBeforeMs,
        });
        const client = createWebDavClient({
            baseUrl: options.baseUrl,
            prefix: options.prefix,
            token,
            fetcher: options.fetcher,
            credentials: options.credentials,
        });
        const appDir = resolveAppDir(options);
        if (appDir && options.ensureAppDir !== false) {
            await client.ensureDirectory(appDir);
        }
        return {
            client,
            token,
            appDir,
            session,
            root,
        };
    }
    async function initDappSession(options) {
        if (!options.appAuth && !options.webdav) {
            throw new Error('No init options provided');
        }
        const provider = options.provider ||
            options.appAuth?.provider ||
            options.webdav?.provider ||
            (await requireProvider());
        const result = {
            provider,
            address: options.address,
        };
        if (options.appAuth) {
            const appLogin = await loginWithChallenge({
                ...options.appAuth,
                provider: options.appAuth.provider || provider,
                address: options.appAuth.address || options.address,
            });
            result.appLogin = appLogin;
            result.address = appLogin.address;
        }
        if (options.webdav) {
            const webdav = await initWebDavStorage({
                ...options.webdav,
                provider: options.webdav.provider || provider,
            });
            result.ucanSession = webdav.session;
            result.ucanRoot = webdav.root;
            result.webdavClient = webdav.client;
            result.webdavToken = webdav.token;
            result.webdavAppDir = webdav.appDir;
        }
        return result;
    }

    exports.DEFAULT_UCAN_SESSION_TTL_MS = DEFAULT_UCAN_SESSION_TTL_MS;
    exports.DEFAULT_UCAN_TOKEN_SKEW_MS = DEFAULT_UCAN_TOKEN_SKEW_MS;
    exports.DEFAULT_UCAN_TOKEN_TTL_MS = DEFAULT_UCAN_TOKEN_TTL_MS;
    exports.WebDavClient = WebDavClient;
    exports.authCentralUcanFetch = authCentralUcanFetch;
    exports.authFetch = authFetch;
    exports.authUcanFetch = authUcanFetch;
    exports.classifyUcanAuthError = classifyUcanAuthError;
    exports.classifyWalletError = classifyWalletError;
    exports.clearAccessToken = clearAccessToken;
    exports.clearCentralSessionToken = clearCentralSessionToken;
    exports.clearUcanSession = clearUcanSession;
    exports.createAndIssueCentralUcan = createAndIssueCentralUcan;
    exports.createCentralSession = createCentralSession;
    exports.createDelegationUcan = createDelegationUcan;
    exports.createInvocationUcan = createInvocationUcan;
    exports.createRootUcan = createRootUcan;
    exports.createUcanSession = createUcanSession;
    exports.createWebDavClient = createWebDavClient;
    exports.decodeUcanPayload = decodeUcanPayload;
    exports.deriveAppIdFromHost = deriveAppIdFromHost;
    exports.deriveAppIdFromLocation = deriveAppIdFromLocation;
    exports.focusPendingApproval = focusPendingApproval;
    exports.getAccessToken = getAccessToken;
    exports.getAccounts = getAccounts;
    exports.getBalance = getBalance;
    exports.getCapabilityAction = getCapabilityAction;
    exports.getCapabilityResource = getCapabilityResource;
    exports.getCentralIssuerInfo = getCentralIssuerInfo;
    exports.getCentralSessionToken = getCentralSessionToken;
    exports.getChainId = getChainId;
    exports.getOrCreateInvocationUcan = getOrCreateInvocationUcan;
    exports.getOrCreateUcanRoot = getOrCreateUcanRoot;
    exports.getPreferredAccount = getPreferredAccount;
    exports.getProvider = getProvider;
    exports.getStoredUcanRoot = getStoredUcanRoot;
    exports.getUcanSession = getUcanSession;
    exports.getUcanTokenTiming = getUcanTokenTiming;
    exports.getWalletErrorCode = getWalletErrorCode;
    exports.getWalletErrorMessage = getWalletErrorMessage;
    exports.initDappSession = initDappSession;
    exports.initWebDavStorage = initWebDavStorage;
    exports.isUcanTokenFresh = isUcanTokenFresh;
    exports.isUserRejectedWalletAction = isUserRejectedWalletAction;
    exports.isWalletReconnectError = isWalletReconnectError;
    exports.isYeYingProvider = isYeYingProvider;
    exports.issueCentralUcan = issueCentralUcan;
    exports.loginWithChallenge = loginWithChallenge;
    exports.logout = logout;
    exports.normalizeAppHostnameForAppId = normalizeAppHostnameForAppId;
    exports.normalizeUcanCapabilities = normalizeUcanCapabilities;
    exports.normalizeUcanCapability = normalizeUcanCapability;
    exports.normalizeUcanExpiry = normalizeUcanExpiry;
    exports.onAccountsChanged = onAccountsChanged;
    exports.onChainChanged = onChainChanged;
    exports.refreshAccessToken = refreshAccessToken;
    exports.requestAccounts = requestAccounts;
    exports.requireProvider = requireProvider;
    exports.setAccessToken = setAccessToken;
    exports.setCentralSessionToken = setCentralSessionToken;
    exports.signMessage = signMessage;
    exports.storeUcanRoot = storeUcanRoot;
    exports.watchAccounts = watchAccounts;
    exports.watchProvider = watchProvider;

}));
//# sourceMappingURL=web3-bs.umd.js.map
