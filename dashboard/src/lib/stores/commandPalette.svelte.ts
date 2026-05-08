import { SvelteMap } from 'svelte/reactivity';

/**
 * Command palette action registry.
 *
 * Components register actions via registerActions(), which returns an
 * unregister function (suitable for onDestroy / $effect cleanup).
 * The palette reads allActions to get every currently-registered action
 * whose available() predicate returns true.
 */

export interface PaletteAction {
	id: string;
	label: string;
	icon?: string;
	category: 'action' | 'navigation' | 'branch';
	/** Return true when this action should appear in the palette. */
	available: () => boolean;
	execute: () => void;
}

// Internal mutable set of all registered actions, keyed by id.
// A simple counter is used to signal changes without replacing the Map.
const registry = new SvelteMap<string, PaletteAction>();
let registryVersion = $state(0);

/**
 * Register one or more actions. Returns a cleanup function that
 * removes exactly those actions from the registry.
 *
 * Uses untrack to prevent callers inside $effect blocks from creating
 * an infinite reactive loop (effect writes state -> re-triggers effect).
 */
export function registerActions(actions: PaletteAction[]): () => void {
	for (const a of actions) {
		registry.set(a.id, a);
	}
	registryVersion++;

	return () => {
		for (const a of actions) {
			registry.delete(a.id);
		}
		registryVersion++;
	};
}

/**
 * Reactive getter: all registered actions whose available() returns true.
 * Reads registryVersion to subscribe to changes.
 */
export function getAllActions(): PaletteAction[] {
	// Read the version counter to establish a reactive dependency
	void registryVersion;
	const out: PaletteAction[] = [];
	for (const action of registry.values()) {
		try {
			if (action.available()) out.push(action);
		} catch {
			// Skip actions whose predicate throws (e.g. during teardown).
		}
	}
	return out;
}
