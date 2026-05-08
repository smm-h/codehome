import { describe, it, expect, vi, beforeEach } from 'vitest';

// Auth store uses $state and imports rune-based modules. Mock everything.
vi.mock('svelte/internal/client', async (importOriginal) => {
	const actual = await importOriginal<Record<string, unknown>>();
	return {
		...actual,
		user_effect: vi.fn(),
	};
});

// vi.hoisted runs before vi.mock factories, making mockApi available.
const mockApi = vi.hoisted(() => ({
	get: vi.fn(),
	post: vi.fn(),
	put: vi.fn(),
	patch: vi.fn(),
	del: vi.fn(),
}));

// Custom ApiError class shared between mock and tests.
const MockedApiError = vi.hoisted(() => {
	return class ApiError extends Error {
		status: number;
		constructor(status: number, message: string) {
			super(message);
			this.status = status;
		}
	};
});

vi.mock('$lib/api', () => ({
	api: mockApi,
	ApiError: MockedApiError,
}));

// Mock rune-based transitive dependencies of the api module.
vi.mock('$lib/stores/connectivity.svelte.js', () => ({
	connectivity: { status: 'online', isOffline: false },
}));
vi.mock('$lib/stores/toast.svelte.js', () => ({
	toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('$lib/i18n/index.svelte.js', () => ({
	i18n: { t: (key: string) => key },
}));

const { auth } = await import('./auth.svelte');

beforeEach(() => {
	vi.clearAllMocks();
});

describe('auth.checkAuth', () => {
	it('sets user on successful /api/auth/me', async () => {
		mockApi.get.mockResolvedValue({ username: 'alice', role: 'admin' });
		await auth.checkAuth();
		expect(auth.user).toEqual({ username: 'alice', role: 'admin' });
		expect(auth.loading).toBe(false);
	});

	it('sets user to null on failure', async () => {
		mockApi.get.mockRejectedValue(new Error('Network error'));
		await auth.checkAuth();
		expect(auth.user).toBeNull();
		expect(auth.loading).toBe(false);
	});
});

describe('auth.login', () => {
	it('returns true on successful login', async () => {
		mockApi.post.mockResolvedValue({ token: 'jwt123' });
		mockApi.get.mockResolvedValue({ username: 'alice', role: 'viewer' });
		const result = await auth.login('alice', 'pass');
		expect(result).toBe(true);
		expect(mockApi.post).toHaveBeenCalledWith('/api/auth/login', {
			username: 'alice',
			password: 'pass',
		});
	});

	it('returns false on 401', async () => {
		mockApi.post.mockRejectedValue(new MockedApiError(401, 'Unauthorized'));
		const result = await auth.login('alice', 'wrong');
		expect(result).toBe(false);
	});

	it('rethrows non-401 errors', async () => {
		mockApi.post.mockRejectedValue(new Error('Server down'));
		await expect(auth.login('alice', 'pass')).rejects.toThrow('Server down');
	});
});

describe('auth.logout', () => {
	it('clears user state after logout', async () => {
		// First, set user to something
		mockApi.get.mockResolvedValue({ username: 'alice', role: 'admin' });
		await auth.checkAuth();
		expect(auth.user).not.toBeNull();

		mockApi.post.mockResolvedValue(null);
		await auth.logout();
		expect(auth.user).toBeNull();
	});

	it('clears user state even if logout API call fails', async () => {
		mockApi.get.mockResolvedValue({ username: 'bob', role: 'viewer' });
		await auth.checkAuth();

		// logout uses try/finally (no catch), so the error propagates
		// but user is still cleared in the finally block.
		mockApi.post.mockRejectedValue(new Error('Network error'));
		await expect(auth.logout()).rejects.toThrow('Network error');
		expect(auth.user).toBeNull();
	});
});

describe('auth computed getters', () => {
	it('isAuthenticated reflects user presence', async () => {
		mockApi.get.mockResolvedValue({ username: 'alice', role: 'viewer' });
		await auth.checkAuth();
		expect(auth.isAuthenticated).toBe(true);

		mockApi.post.mockResolvedValue(null);
		await auth.logout();
		expect(auth.isAuthenticated).toBe(false);
	});

	it('isAdmin is true only for admin role', async () => {
		mockApi.get.mockResolvedValue({ username: 'alice', role: 'admin' });
		await auth.checkAuth();
		expect(auth.isAdmin).toBe(true);

		mockApi.get.mockResolvedValue({ username: 'bob', role: 'viewer' });
		await auth.checkAuth();
		expect(auth.isAdmin).toBe(false);
	});
});
