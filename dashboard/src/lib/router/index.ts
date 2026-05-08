/**
 * Router module barrel export.
 *
 * Usage:
 *   import { goto, page, initRouter, getPath, getMatch } from '$lib/router';
 *   import Router from '$lib/router/Router.svelte';
 *   import Link from '$lib/router/Link.svelte';
 */

export { initRouter, addRoutes, goto, getPath, getMatch, page } from './router.svelte.js';
export type { RouteDefinition, MatchedRoute } from './router.svelte.js';
export { routeTable, injectPluginRoutes } from './routes.js';
