/**
 * Plugin discovery store.
 *
 * Fetches the list of loaded plugins from /api/plugins and exposes
 * reactive state for the rest of the dashboard to consume:
 *   - plugin metadata (name, version, has_dashboard, etc.)
 *   - derived Tab entries for plugins with dashboard integration
 *   - SSE event types declared by plugins
 *
 * The store is loaded once after authentication and is not expected
 * to change during a session (plugins are loaded at server startup).
 */

import { reportError } from '$lib/errors';
import type { Tab } from '$lib/tabs.js';

/** Shape of the dashboard metadata returned by GET /api/plugins. */
export interface PluginDashboard {
	group: 'root' | 'branch';
	route: string;
	icon: string;
	label: string;
	event_types: string[];
}

export interface PluginInfo {
	name: string;
	version: string;
	description: string;
	has_dashboard: boolean;
	has_cli: boolean;
	has_checks: boolean;
	dashboard: PluginDashboard | null;
}

function createPluginsStore() {
	let plugins = $state<PluginInfo[]>([]);
	let loaded = $state(false);

	async function load() {
		try {
			const resp = await fetch('/api/plugins');
			if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
			plugins = await resp.json();
		} catch (e) {
			reportError(e, { silent: true, category: 'plugins' });
			plugins = [];
		}
		loaded = true;
	}

	/** Plugins that declare dashboard integration. */
	function withDashboard(): PluginInfo[] {
		return plugins.filter((p) => p.has_dashboard && p.dashboard);
	}

	/**
	 * Convert dashboard plugins into Tab entries compatible with the
	 * existing TabBar component.
	 *
	 * The `i18n` field is set to the plugin's label directly -- the
	 * i18n.t() function falls back to the key itself when no translation
	 * exists, so literal labels pass through cleanly.
	 */
	function tabs(): Tab[] {
		return withDashboard().map((p) => {
			// withDashboard() guarantees p.dashboard is non-null
			const d = p.dashboard as PluginDashboard;
			return {
				key: `plugin:${p.name}`,
				icon: d.icon || 'puzzle',
				i18n: d.label || p.name,
				scope: d.group === 'branch' ? ('branch' as const) : ('global' as const),
			};
		});
	}

	/** Collect all SSE event types declared by dashboard plugins. */
	function eventTypes(): string[] {
		const types: string[] = [];
		for (const p of withDashboard()) {
			if (p.dashboard?.event_types) {
				types.push(...p.dashboard.event_types);
			}
		}
		return types;
	}

	return {
		get plugins() {
			return plugins;
		},
		get loaded() {
			return loaded;
		},
		load,
		withDashboard,
		tabs,
		eventTypes,
	};
}

export const pluginStore = createPluginsStore();
