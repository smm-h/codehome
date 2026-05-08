import { describe, it, expect } from 'vitest';
import { TABS } from './tabs';
import type { Tab } from './tabs';

describe('TABS', () => {
	it('is a non-empty array', () => {
		expect(TABS.length).toBeGreaterThan(0);
	});

	it('every tab has required fields', () => {
		for (const tab of TABS) {
			expect(tab).toHaveProperty('key');
			expect(tab).toHaveProperty('icon');
			expect(tab).toHaveProperty('i18n');
			expect(tab).toHaveProperty('scope');
			expect(typeof tab.key).toBe('string');
			expect(typeof tab.icon).toBe('string');
			expect(typeof tab.i18n).toBe('string');
			expect(['global', 'branch']).toContain(tab.scope);
		}
	});

	it('has unique keys', () => {
		const keys = TABS.map((t: Tab) => t.key);
		expect(new Set(keys).size).toBe(keys.length);
	});

	it('contains the core shell tabs', () => {
		const keys = TABS.map((t: Tab) => t.key);
		expect(keys).toContain('home');
		expect(keys).toContain('system');
		expect(keys).toContain('settings');
	});

	it('has global scoped tabs (branch tabs contributed by plugins)', () => {
		const globalTabs = TABS.filter((t: Tab) => t.scope === 'global');
		expect(globalTabs.length).toBeGreaterThan(0);
	});
});

