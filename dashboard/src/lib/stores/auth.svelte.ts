import { api, ApiError } from '$lib/api';
import { reportError } from '$lib/errors';

interface User {
	username: string;
	role: string;
}

function createAuthStore() {
	let user = $state<User | null>(null);
	let loading = $state(true);

	async function checkAuth() {
		loading = true;
		try {
			user = await api.get<User | null>('/api/auth/me');
		} catch (e) {
			reportError(e, { silent: true, category: 'auth' });
			user = null;
		} finally {
			loading = false;
		}
	}

	async function login(username: string, password: string): Promise<boolean> {
		try {
			const res = await api.post<{ token?: string }>('/api/auth/login', { username, password });
			// Store the JWT for WebSocket auth (session cookie is httpOnly).
			if (res?.token && typeof sessionStorage !== 'undefined') {
				sessionStorage.setItem('auth-token', res.token);
			}
			await checkAuth();
			return true;
		} catch (e) {
			if (e instanceof ApiError && e.status === 401) return false;
			throw e;
		}
	}

	async function logout() {
		try {
			await api.post('/api/auth/logout');
		} finally {
			user = null;
			if (typeof sessionStorage !== 'undefined') {
				sessionStorage.removeItem('auth-token');
			}
		}
	}

	return {
		get user() {
			return user;
		},
		get loading() {
			return loading;
		},
		get isAuthenticated() {
			return user !== null;
		},
		get isAdmin() {
			return user?.role === 'admin';
		},
		checkAuth,
		login,
		logout,
	};
}

export const auth = createAuthStore();
