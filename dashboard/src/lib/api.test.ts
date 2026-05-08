import { describe, it, expect, vi, beforeEach } from 'vitest';

// api.ts transitively imports Svelte rune-based stores (connectivity,
// toast, i18n) that call $state/$effect at module level.
// These crash outside a Svelte component context, so mock them all.
vi.mock('$lib/stores/connectivity.svelte.js', () => ({
	connectivity: { status: 'online', isOffline: false, browserOnline: true },
}));
vi.mock('$lib/stores/toast.svelte.js', () => ({
	toast: { error: vi.fn(), warning: vi.fn(), info: vi.fn(), success: vi.fn() },
}));
vi.mock('$lib/i18n/index.svelte.js', () => ({
	i18n: { t: (key: string) => key },
}));

import { api, ApiError } from './api';

// Mock the global fetch for all tests in this file
beforeEach(() => {
	vi.restoreAllMocks();
});

/** Helper: create a minimal Response-like object for fetch mocking. */
function mockResponse(status: number, body: string, ok?: boolean): Response {
	return {
		ok: ok ?? (status >= 200 && status < 300),
		status,
		statusText: `HTTP ${status}`,
		text: () => Promise.resolve(body),
		headers: new Headers(),
	} as Response;
}

describe('api.get', () => {
	it('parses a JSON response', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(200, '{"id":1}')));
		const result = await api.get('/api/test');
		expect(result).toEqual({ id: 1 });
	});

	it('returns null for empty responses', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(204, '')));
		const result = await api.get('/api/empty');
		expect(result).toBeNull();
	});

	it('sends GET method without body', async () => {
		const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, '{}'));
		vi.stubGlobal('fetch', fetchMock);
		await api.get('/api/test');
		expect(fetchMock).toHaveBeenCalledWith('/api/test', {
			method: 'GET',
			headers: {},
			body: undefined,
			credentials: 'include',
			signal: expect.any(AbortSignal),
		});
	});
});

describe('api.post', () => {
	it('sends JSON body with Content-Type header', async () => {
		const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, '{}'));
		vi.stubGlobal('fetch', fetchMock);
		await api.post('/api/create', { name: 'test' });
		expect(fetchMock).toHaveBeenCalledWith('/api/create', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: '{"name":"test"}',
			credentials: 'include',
			signal: expect.any(AbortSignal),
		});
	});
});

describe('error handling', () => {
	it('throws ApiError with detail from FastAPI JSON response', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(404, '{"detail":"Not found"}')));
		await expect(api.get('/api/missing')).rejects.toThrow(ApiError);
		try {
			await api.get('/api/missing');
		} catch (e) {
			expect(e).toBeInstanceOf(ApiError);
			expect((e as ApiError).status).toBe(404);
			expect((e as ApiError).message).toBe('Not found');
		}
	});

	it('extracts "message" field from JSON error responses', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(400, '{"message":"Bad input"}')));
		await expect(api.get('/api/bad')).rejects.toThrow('Bad input');
	});

	it('extracts "error" field from JSON error responses', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(mockResponse(500, '{"error":"Internal failure"}')),
		);
		await expect(api.get('/api/crash')).rejects.toThrow('Internal failure');
	});

	it('falls back to raw text when response is not JSON', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(502, 'Bad Gateway')));
		await expect(api.get('/api/down')).rejects.toThrow('Bad Gateway');
	});

	it('falls back to HTTP status when body is empty', async () => {
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(503, '')));
		await expect(api.get('/api/down')).rejects.toThrow('HTTP 503');
	});
});

describe('api methods', () => {
	it('api.put sends PUT method', async () => {
		const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, '{}'));
		vi.stubGlobal('fetch', fetchMock);
		await api.put('/api/update', { v: 1 });
		expect(fetchMock.mock.calls[0][1].method).toBe('PUT');
	});

	it('api.patch sends PATCH method', async () => {
		const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, '{}'));
		vi.stubGlobal('fetch', fetchMock);
		await api.patch('/api/patch', { v: 1 });
		expect(fetchMock.mock.calls[0][1].method).toBe('PATCH');
	});

	it('api.del sends DELETE method', async () => {
		const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, '{}'));
		vi.stubGlobal('fetch', fetchMock);
		await api.del('/api/remove');
		expect(fetchMock.mock.calls[0][1].method).toBe('DELETE');
	});
});
