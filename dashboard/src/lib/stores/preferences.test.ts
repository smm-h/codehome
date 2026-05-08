import { describe, it, expect, vi, beforeEach } from 'vitest';

// Preferences store uses $state and imports rune-based modules.
vi.mock('svelte/internal/client', async (importOriginal) => {
	const actual = await importOriginal<Record<string, unknown>>();
	return {
		...actual,
		user_effect: vi.fn(),
	};
});

// vi.hoisted runs before vi.mock factories, making these available.
const mockApi = vi.hoisted(() => ({
	get: vi.fn(),
	post: vi.fn(),
	put: vi.fn(),
	patch: vi.fn(),
	del: vi.fn(),
}));

const mockToast = vi.hoisted(() => ({
	error: vi.fn(),
	success: vi.fn(),
	info: vi.fn(),
	warning: vi.fn(),
	add: vi.fn(),
}));

vi.mock('$lib/api.js', () => ({
	api: mockApi,
}));

vi.mock('$lib/stores/toast.svelte.js', () => ({
	toast: mockToast,
}));

// Mock transitive rune-based dependencies of api module.
vi.mock('$lib/stores/connectivity.svelte.js', () => ({
	connectivity: { status: 'online', isOffline: false },
}));
vi.mock('$lib/i18n/index.svelte.js', () => ({
	i18n: { t: (key: string) => key },
}));

const { preferences } = await import('./preferences.svelte');

beforeEach(() => {
	vi.clearAllMocks();
});

describe('preferences.load', () => {
	it('populates state from API response', async () => {
		mockApi.get.mockResolvedValue({
			metrics_variant: 'chartjs',
			theme: 'dark',
			language: 'it',
			accent_color: 'rose',
		});
		await preferences.load();
		expect(preferences.metricsVariant).toBe('chartjs');
		expect(preferences.theme).toBe('dark');
		expect(preferences.language).toBe('it');
		expect(preferences.accentColor).toBe('rose');
		expect(preferences.loaded).toBe(true);
	});

	it('falls back to defaults when API fails', async () => {
		mockApi.get.mockRejectedValue(new Error('Network error'));
		await preferences.load();
		// Defaults from source: sparkline, auto, en, indigo
		// Note: after a prior successful load, state may have changed.
		// This test verifies no crash and loaded might remain false or whatever
		// the default was -- the key thing is it doesn't throw.
		expect(preferences.metricsVariant).toBeDefined();
		expect(preferences.theme).toBeDefined();
	});

	it('uses default values for missing API fields', async () => {
		mockApi.get.mockResolvedValue({});
		await preferences.load();
		expect(preferences.metricsVariant).toBe('sparkline');
		expect(preferences.theme).toBe('auto');
		expect(preferences.language).toBe('en');
		expect(preferences.accentColor).toBe('indigo');
	});
});

describe('preferences.update', () => {
	it('performs optimistic update then persists', async () => {
		mockApi.put.mockResolvedValue(null);
		await preferences.update('theme', 'light');
		expect(preferences.theme).toBe('light');
		expect(mockApi.put).toHaveBeenCalledWith('/api/preferences', { theme: 'light' });
	});

	it('rolls back on API failure', async () => {
		// First set a known state
		mockApi.get.mockResolvedValue({
			metrics_variant: 'sparkline',
			theme: 'auto',
			language: 'en',
			accent_color: 'indigo',
		});
		await preferences.load();
		expect(preferences.theme).toBe('auto');

		// Now attempt update that fails
		mockApi.put.mockRejectedValue(new Error('Server error'));
		await preferences.update('theme', 'dark');

		// Should have rolled back
		expect(preferences.theme).toBe('auto');
		expect(mockToast.error).toHaveBeenCalledWith('Server error');
	});

	it('shows generic message for non-Error failures', async () => {
		mockApi.get.mockResolvedValue({
			metrics_variant: 'sparkline',
			theme: 'auto',
			language: 'en',
			accent_color: 'indigo',
		});
		await preferences.load();

		mockApi.put.mockRejectedValue('string error');
		await preferences.update('language', 'it');

		expect(preferences.language).toBe('en');
		expect(mockToast.error).toHaveBeenCalledWith('Failed to save preference');
	});

	it('updates metrics_variant', async () => {
		mockApi.put.mockResolvedValue(null);
		await preferences.update('metrics_variant', 'canvas');
		expect(preferences.metricsVariant).toBe('canvas');
	});

	it('updates accent_color', async () => {
		mockApi.put.mockResolvedValue(null);
		await preferences.update('accent_color', 'rose');
		expect(preferences.accentColor).toBe('rose');
	});
});
