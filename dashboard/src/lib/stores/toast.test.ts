import { describe, it, expect, vi, beforeEach } from 'vitest';

// Toast store uses $state at module level. Mock Svelte internals before import.
vi.mock('svelte/internal/client', async (importOriginal) => {
	const actual = await importOriginal<Record<string, unknown>>();
	return {
		...actual,
		user_effect: vi.fn(),
	};
});

const { toast } = await import('./toast.svelte');

beforeEach(() => {
	// Dismiss all existing toasts to start each test clean.
	for (const t of [...toast.toasts]) {
		toast.dismiss(t.id);
	}
});

describe('toast.add', () => {
	it('adds a toast with the given level and string message', () => {
		toast.add('info', 'hello');
		const t = toast.toasts[toast.toasts.length - 1];
		expect(t.level).toBe('info');
		expect(t.message).toBe('hello');
	});

	it('assigns unique incrementing IDs', () => {
		toast.add('info', 'first');
		toast.add('info', 'second');
		const ids = toast.toasts.map((t) => t.id);
		// Each id should be unique and the second should be greater
		expect(ids[ids.length - 1]).toBeGreaterThan(ids[ids.length - 2]);
	});
});

describe('level shortcuts', () => {
	it('error() adds an error-level toast', () => {
		toast.error('boom');
		const t = toast.toasts[toast.toasts.length - 1];
		expect(t.level).toBe('error');
		expect(t.message).toBe('boom');
	});

	it('warning() adds a warning-level toast', () => {
		toast.warning('careful');
		const t = toast.toasts[toast.toasts.length - 1];
		expect(t.level).toBe('warning');
		expect(t.message).toBe('careful');
	});

	it('info() adds an info-level toast', () => {
		toast.info('fyi');
		const t = toast.toasts[toast.toasts.length - 1];
		expect(t.level).toBe('info');
		expect(t.message).toBe('fyi');
	});

	it('success() adds a success-level toast', () => {
		toast.success('done');
		const t = toast.toasts[toast.toasts.length - 1];
		expect(t.level).toBe('success');
		expect(t.message).toBe('done');
	});
});

describe('message coercion', () => {
	it('coerces an Error instance to its .message', () => {
		toast.add('error', new Error('something broke'));
		const t = toast.toasts[toast.toasts.length - 1];
		expect(t.message).toBe('something broke');
	});

	it('coerces an object with .detail string', () => {
		toast.add('error', { detail: 'detail text' });
		expect(toast.toasts[toast.toasts.length - 1].message).toBe('detail text');
	});

	it('coerces an object with .message string', () => {
		toast.add('error', { message: 'msg text' });
		expect(toast.toasts[toast.toasts.length - 1].message).toBe('msg text');
	});

	it('coerces an object with .error string', () => {
		toast.add('error', { error: 'err text' });
		expect(toast.toasts[toast.toasts.length - 1].message).toBe('err text');
	});

	it('prefers .detail over .message over .error', () => {
		toast.add('error', { detail: 'wins', message: 'loses', error: 'also loses' });
		expect(toast.toasts[toast.toasts.length - 1].message).toBe('wins');
	});

	it('falls back to JSON.stringify for object without known keys', () => {
		toast.add('error', { foo: 'bar' });
		expect(toast.toasts[toast.toasts.length - 1].message).toBe('{"foo":"bar"}');
	});

	it('coerces a number via String()', () => {
		toast.add('info', 42);
		expect(toast.toasts[toast.toasts.length - 1].message).toBe('42');
	});

	it('coerces null via String()', () => {
		toast.add('info', null);
		expect(toast.toasts[toast.toasts.length - 1].message).toBe('null');
	});

	it('coerces undefined via String()', () => {
		toast.add('info', undefined);
		expect(toast.toasts[toast.toasts.length - 1].message).toBe('undefined');
	});
});

describe('toast.dismiss', () => {
	it('removes the toast with the given id', () => {
		toast.add('info', 'a', 0);
		toast.add('info', 'b', 0);
		toast.add('info', 'c', 0);
		const match = toast.toasts.find((t) => t.message === 'b');
		expect(match).toBeDefined();
		const idB = (match as NonNullable<typeof match>).id;
		toast.dismiss(idB);
		expect(toast.toasts.find((t) => t.id === idB)).toBeUndefined();
		expect(toast.toasts.map((t) => t.message)).toContain('a');
		expect(toast.toasts.map((t) => t.message)).toContain('c');
	});

	it('is a no-op when id does not exist', () => {
		toast.add('info', 'keep', 0);
		const before = toast.toasts.length;
		toast.dismiss(-999);
		expect(toast.toasts.length).toBe(before);
	});
});
