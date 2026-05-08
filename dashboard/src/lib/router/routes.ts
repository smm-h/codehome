/**
 * Route table for the dashboard shell.
 *
 * Only shell routes are defined here. Domain-specific pages are contributed
 * by plugins at runtime via `injectPluginRoutes()`.
 *
 * Components are lazy-loaded via dynamic import so initial bundle stays small.
 *
 * Layout values:
 *   'root'   - wrapped in RootLayout (app shell with nav, auth, SSE)
 *   'branch' - wrapped in BranchLayout (nested inside RootLayout; adds tab bar, breadcrumbs)
 *   'none'   - no layout (login page)
 */

import type { RouteDefinition } from './router.svelte.js';
import type { PluginInfo } from '$lib/stores/plugins.svelte.js';
import { addRoutes } from './router.svelte.js';

export const routeTable: RouteDefinition[] = [
	{
		path: '/',
		component: () => import('../../pages/PluginPage.svelte'),
		layout: 'root',
	},
	{
		path: '/login',
		component: () => import('../../pages/Login.svelte'),
		layout: 'none',
	},
];

/**
 * Build route definitions from plugin metadata and inject them into
 * the live router.
 *
 * Called once after fetching /api/plugins. Plugins with
 * `has_dashboard: true` get a route that lazy-loads PluginPage.svelte.
 *
 * - Root-group plugins: route at their declared path (e.g. `/hubspot`)
 * - Branch-group plugins: route at `/branch/:repo/:branch/<route>`
 */
export function injectPluginRoutes(plugins: PluginInfo[]): void {
	const defs: RouteDefinition[] = [];

	for (const plugin of plugins) {
		if (!plugin.has_dashboard || !plugin.dashboard) continue;
		const { group, route } = plugin.dashboard;
		// Normalize: ensure route starts with /
		const normalizedRoute = route.startsWith('/') ? route : `/${route}`;

		if (group === 'root') {
			defs.push({
				path: normalizedRoute,
				component: () => import('../../pages/PluginPage.svelte'),
				layout: 'root',
			});
		} else if (group === 'branch') {
			defs.push({
				path: `/branch/:repo/:branch${normalizedRoute}`,
				component: () => import('../../pages/PluginPage.svelte'),
				layout: 'branch',
			});
		}
	}

	if (defs.length > 0) {
		addRoutes(defs);
	}
}
