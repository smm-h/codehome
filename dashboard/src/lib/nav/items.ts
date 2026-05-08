import type { NavItem } from './types';
import type { PluginInfo } from '$lib/stores/plugins.svelte.js';

/** Core shell nav items. Domain-specific items are contributed by plugins. */
export function getCoreNavItems(): NavItem[] {
	return [
		{ key: 'home', label: 'Home', icon: 'home', path: '/', source: 'core', visible: true },
	];
}

export function pluginNavItems(plugins: PluginInfo[]): NavItem[] {
	return plugins
		.filter((p) => p.has_dashboard && p.dashboard?.group === 'root')
		.map((p) => ({
			key: p.name,
			label: p.dashboard!.label || p.name,
			icon: p.dashboard!.icon || 'puzzle',
			path: p.dashboard!.route,
			source: 'plugin' as const,
			visible: true,
		}));
}

export function buildNavItems(
	coreItems: NavItem[],
	pluginItems: NavItem[],
	userOrder?: string[],
): NavItem[] {
	const all = [...coreItems, ...pluginItems];
	if (!userOrder || userOrder.length === 0) return all.filter((i) => i.visible);

	const byKey = new Map(all.map((i) => [i.key, i]));
	const ordered: NavItem[] = [];

	for (const key of userOrder) {
		const item = byKey.get(key);
		if (item) {
			ordered.push(item);
			byKey.delete(key);
		}
	}

	for (const item of byKey.values()) {
		ordered.push(item);
	}

	return ordered.filter((i) => i.visible);
}

export function activeItemForPath(path: string, items: NavItem[]): string {
	let bestKey = '';
	let bestLen = -1;

	for (const item of items) {
		if (!item.visible) continue;
		if (item.path === '/') {
			if (path === '/' || path === '') {
				if (bestLen < 1) {
					bestKey = item.key;
					bestLen = 1;
				}
			}
			continue;
		}
		if (path === item.path || path.startsWith(item.path + '/')) {
			if (item.path.length > bestLen) {
				bestKey = item.key;
				bestLen = item.path.length;
			}
		}
	}

	return bestKey;
}
