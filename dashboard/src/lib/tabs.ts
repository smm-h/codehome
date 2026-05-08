export interface Tab {
	key: string;
	icon: string;
	i18n: string;
	scope: 'global' | 'branch';
	/** Feature flag that must be enabled for this tab to appear. */
	flag?: string;
}

/**
 * Core shell tabs. Domain-specific tabs are contributed by plugins
 * at runtime via registerPluginTabs().
 */
export const TABS: readonly Tab[] = [
	{ key: 'home', icon: 'home', i18n: 'nav.home', scope: 'global' },
	{ key: 'system', icon: 'settings', i18n: 'nav.system', scope: 'global' },
	{ key: 'settings', icon: 'user', i18n: 'nav.settings', scope: 'global' },
] as const;

/**
 * Plugin-contributed tabs, populated at runtime after fetching /api/plugins.
 *
 * Mutated by `registerPluginTabs()` and read by components that need
 * the combined tab list. This is a plain mutable array (not reactive)
 * because it's set once during init before the UI renders plugin tabs.
 */
let _pluginTabs: Tab[] = [];

/** Register plugin-contributed tabs. Called once after plugin discovery. */
export function registerPluginTabs(tabs: Tab[]): void {
	_pluginTabs = tabs;
}

/**
 * Return the full tab list: core TABS + any plugin-contributed tabs.
 *
 * Plugin tabs for the 'global' scope are appended before the system/settings
 * cluster; 'branch' scope tabs are appended after the last core branch tab.
 */
export function allTabs(): readonly Tab[] {
	if (_pluginTabs.length === 0) return TABS;

	const result: Tab[] = [];
	const globalPlugins = _pluginTabs.filter((t) => t.scope === 'global');
	const branchPlugins = _pluginTabs.filter((t) => t.scope === 'branch');

	// Insert branch plugins after the last core branch tab ('todo'),
	// and global plugins before the trailing system cluster ('docs' onward).
	let insertedBranch = false;
	let insertedGlobal = false;

	for (const tab of TABS) {
		// Insert branch plugins after the last branch-scoped core tab
		if (!insertedBranch && tab.scope === 'global' && branchPlugins.length > 0) {
			result.push(...branchPlugins);
			insertedBranch = true;
		}
		// Insert global plugins before the system/settings cluster
		if (
			!insertedGlobal &&
			(tab.key === 'system' || tab.key === 'settings') &&
			globalPlugins.length > 0
		) {
			result.push(...globalPlugins);
			insertedGlobal = true;
		}
		result.push(tab);
	}

	// Append any remaining plugin tabs that didn't get inserted
	if (!insertedBranch) result.push(...branchPlugins);
	if (!insertedGlobal) result.push(...globalPlugins);

	return result;
}
