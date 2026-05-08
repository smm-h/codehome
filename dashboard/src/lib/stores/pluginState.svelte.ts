/**
 * Per-plugin reactive state store for SDUI expression resolution.
 *
 * Each plugin gets its own state namespace (keyed by plugin name).
 * The SDUIRenderer reads from this store when evaluating ${state.x}
 * expressions. State is populated by SSE events from the plugin's
 * backend and can also be set programmatically from the PluginPage.
 */

function createPluginStateStore() {
	let states = $state<Record<string, Record<string, unknown>>>({});

	/** Replace the entire state object for a plugin. */
	function set(pluginName: string, state: Record<string, unknown>) {
		states[pluginName] = state;
	}

	/** Get the current state for a plugin (empty object if none). */
	function get(pluginName: string): Record<string, unknown> {
		return states[pluginName] ?? {};
	}

	/** Merge partial updates into a plugin's existing state. */
	function update(pluginName: string, partial: Record<string, unknown>) {
		states[pluginName] = { ...(states[pluginName] ?? {}), ...partial };
	}

	/** Clear all plugin state (e.g. on logout or full refresh). */
	function clear() {
		states = {};
	}

	return {
		get states() {
			return states;
		},
		set,
		get,
		update,
		clear,
	};
}

export const pluginState = createPluginStateStore();
