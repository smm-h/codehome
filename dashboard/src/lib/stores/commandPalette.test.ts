import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { PaletteAction } from './commandPalette.svelte';

// commandPalette uses $state at module level. Mock Svelte internals before import.
vi.mock('svelte/internal/client', async (importOriginal) => {
	const actual = await importOriginal<Record<string, unknown>>();
	return {
		...actual,
		user_effect: vi.fn(),
	};
});

const { registerActions, getAllActions } = await import('./commandPalette.svelte');

/** Helper: create a minimal PaletteAction. */
function action(id: string, overrides: Partial<PaletteAction> = {}): PaletteAction {
	return {
		id,
		label: `Label ${id}`,
		category: 'action',
		available: () => true,
		execute: vi.fn(),
		...overrides,
	};
}

// Track cleanup functions so we can unregister between tests.
let cleanups: (() => void)[] = [];

beforeEach(() => {
	for (const fn of cleanups) fn();
	cleanups = [];
});

describe('registerActions', () => {
	it('registers actions that appear in getAllActions', () => {
		const cleanup = registerActions([action('a'), action('b')]);
		cleanups.push(cleanup);
		const ids = getAllActions().map((a) => a.id);
		expect(ids).toContain('a');
		expect(ids).toContain('b');
	});

	it('returns a cleanup function that removes the registered actions', () => {
		const cleanup = registerActions([action('c')]);
		expect(getAllActions().map((a) => a.id)).toContain('c');
		cleanup();
		expect(getAllActions().map((a) => a.id)).not.toContain('c');
	});

	it('re-registration with the same id overwrites the previous action', () => {
		const first = action('dup', { label: 'First' });
		const second = action('dup', { label: 'Second' });
		const c1 = registerActions([first]);
		cleanups.push(c1);
		const c2 = registerActions([second]);
		cleanups.push(c2);
		const labels = getAllActions().map((a) => a.label);
		// Only one entry with the id, and it should be the second one
		expect(labels.filter((l) => l === 'First')).toHaveLength(0);
		expect(labels.filter((l) => l === 'Second')).toHaveLength(1);
	});
});

describe('getAllActions', () => {
	it('filters out actions whose available() returns false', () => {
		const cleanup = registerActions([
			action('visible', { available: () => true }),
			action('hidden', { available: () => false }),
		]);
		cleanups.push(cleanup);
		const ids = getAllActions().map((a) => a.id);
		expect(ids).toContain('visible');
		expect(ids).not.toContain('hidden');
	});

	it('skips actions whose available() throws', () => {
		const cleanup = registerActions([
			action('ok'),
			action('broken', {
				available: () => {
					throw new Error('teardown');
				},
			}),
		]);
		cleanups.push(cleanup);
		const ids = getAllActions().map((a) => a.id);
		expect(ids).toContain('ok');
		expect(ids).not.toContain('broken');
	});

	it('returns an empty array when no actions are registered', () => {
		expect(getAllActions()).toEqual([]);
	});
});
