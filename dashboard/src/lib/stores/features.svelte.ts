/**
 * Feature flags store.
 *
 * Fetches the feature flag map from the backend and exposes reactive
 * state so the UI can hide disabled features (tabs, routes, etc.).
 *
 * Fails open: if the server is unreachable, all flags default to true
 * so the UI remains fully functional.
 */

import { reportError } from '$lib/errors';
import { sse } from './sse.svelte.js';

type Flags = Record<string, boolean>;

function createFeaturesStore() {
	let flags = $state<Flags>({});
	let loaded = $state(false);

	async function load() {
		try {
			const resp = await fetch('/api/features');
			if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
			const data: Flags = await resp.json();
			flags = data;
		} catch (e) {
			reportError(e, { silent: true, category: 'features' });
			// Fail open: leave flags empty so enabled() returns true for everything.
			flags = {};
		}
		loaded = true;
	}

	/** Re-fetch flags from the server (e.g. after toggling from settings). */
	async function refresh() {
		await load();
	}

	/** Check whether a feature is enabled. Unknown flags default to true (fail-open). */
	function enabled(name: string): boolean {
		if (!(name in flags)) return true;
		return flags[name];
	}

	sse.subscribe('feature.changed', (data: { flags: Flags }) => {
		flags = data.flags;
		loaded = true;
	});

	return {
		get flags() {
			return flags;
		},
		get loaded() {
			return loaded;
		},
		load,
		refresh,
		enabled,
	};
}

export const features = createFeaturesStore();
