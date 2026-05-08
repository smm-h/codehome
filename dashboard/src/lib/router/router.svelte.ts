/**
 * Lightweight client-side router using Svelte 5 runes.
 *
 * Provides reactive state for the current path, matched route, and params.
 * Uses the History API (pushState / popstate) for navigation.
 */

import type { Component } from 'svelte';
import { SvelteSet, SvelteURL } from 'svelte/reactivity';
import { features } from '$lib/stores/features.svelte.js';

// ── Types ──

export interface RouteDefinition {
	/** URL pattern, e.g. '/', '/branch/:repo/:branch', '/remote/:repo/*rest' */
	path: string;
	/** Lazy component loader. Returns the default export of a Svelte module. */
	component: () => Promise<{ default: Component }>;
	/** Which layout to wrap this page in: 'root', 'branch', or 'none' (login). */
	layout: 'root' | 'branch' | 'none';
	/** Feature flag that must be enabled for this route to resolve. */
	flag?: string;
	/** If set, navigating to this path redirects to the given path instead. */
	redirect?: string;
}

export interface MatchedRoute {
	route: RouteDefinition;
	params: Record<string, string>;
}

// ── Route matching ──

interface CompiledRoute {
	def: RouteDefinition;
	regex: RegExp;
	paramNames: string[];
}

/**
 * Compile a path pattern into a regex + param name list.
 *
 * Supports:
 *   - `:name`   -> named param (single segment)
 *   - `*rest`   -> rest/catch-all param (one or more segments)
 */
function compileRoute(def: RouteDefinition): CompiledRoute {
	const paramNames: string[] = [];
	const parts = def.path.split('/').filter(Boolean);
	let pattern = '';

	for (const part of parts) {
		if (part.startsWith('*')) {
			// Rest param: matches one or more remaining segments
			paramNames.push(part.slice(1));
			pattern += '/(.+)';
		} else if (part.startsWith(':')) {
			// Named param: matches a single segment
			paramNames.push(part.slice(1));
			pattern += '/([^/]+)';
		} else {
			pattern += '/' + escapeRegex(part);
		}
	}

	// Empty pattern means root path '/'
	if (!pattern) pattern = '/';

	return {
		def,
		regex: new RegExp('^' + pattern + '/?$'),
		paramNames,
	};
}

function escapeRegex(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// ── Router singleton ──

let routes: CompiledRoute[] = [];
let currentPath = $state(typeof window !== 'undefined' ? window.location.pathname : '/');
let currentMatch = $state<MatchedRoute | null>(null);

/** Try to match a path against the registered routes. */
function matchPath(path: string): MatchedRoute | null {
	for (const compiled of routes) {
		const m = path.match(compiled.regex);
		if (m) {
			const params: Record<string, string> = {};
			compiled.paramNames.forEach((name, i) => {
				params[name] = decodeURIComponent(m[i + 1]);
			});
			return { route: compiled.def, params };
		}
	}
	return null;
}

function updateMatch() {
	const match = matchPath(currentPath);
	if (match?.route.redirect) {
		window.history.replaceState(null, '', match.route.redirect);
		currentPath = match.route.redirect;
		currentMatch = matchPath(match.route.redirect);
		return;
	}
	if (match?.route.flag && !features.enabled(match.route.flag)) {
		currentMatch = null;
		if (currentPath !== '/') {
			window.history.replaceState(null, '', '/');
			currentPath = '/';
			currentMatch = matchPath('/');
		}
		return;
	}
	currentMatch = match;
}

/** Register the full route table. Call once at startup. */
export function initRouter(defs: RouteDefinition[]) {
	routes = defs.map(compileRoute);
	currentPath = window.location.pathname;
	updateMatch();

	// Listen for browser back/forward navigation.
	window.addEventListener('popstate', () => {
		currentPath = window.location.pathname;
		updateMatch();
	});
}

/**
 * Add routes dynamically after initialization.
 *
 * Used by the plugin system to inject plugin-contributed routes after
 * fetching plugin metadata from the server. New routes are inserted
 * before the last entry (the branch landing page catch-all) to preserve
 * route priority. Duplicates (same path) are skipped.
 */
export function addRoutes(defs: RouteDefinition[]) {
	const existingPaths = new SvelteSet(routes.map((r) => r.def.path));
	const newRoutes = defs.filter((d) => !existingPaths.has(d.path)).map(compileRoute);
	if (newRoutes.length === 0) return;

	// Insert before the last route (branch landing page catch-all)
	// so specific plugin paths match before the generic pattern.
	const insertIdx = routes.length > 0 ? routes.length - 1 : 0;
	routes.splice(insertIdx, 0, ...newRoutes);

	// Re-evaluate current path against the expanded route table.
	updateMatch();
}

/**
 * Navigate to a new path, updating state and pushing to browser history.
 *
 * Options:
 *   - replaceState: use replaceState instead of pushState (default false)
 */
export function goto(path: string, opts?: { replaceState?: boolean }) {
	if (path === currentPath) return;
	if (opts?.replaceState) {
		window.history.replaceState(null, '', path);
	} else {
		window.history.pushState(null, '', path);
	}
	currentPath = path;
	updateMatch();
}

// ── Reactive accessors ──

/** Current URL pathname (reactive). */
export function getPath(): string {
	return currentPath;
}

/** Current matched route and params (reactive), or null if no match. */
export function getMatch(): MatchedRoute | null {
	return currentMatch;
}

/**
 * Reactive object mimicking SvelteKit's `page` store shape.
 * Provides `url` (with pathname and searchParams) and `params`.
 *
 * Used as a drop-in migration bridge so pages can do:
 *   import { page } from '$lib/router/page.js';
 * instead of:
 *   import { page } from '$app/state';
 */
export const page = {
	get url() {
		return new SvelteURL(currentPath, window.location.origin);
	},
	get params() {
		return currentMatch?.params ?? {};
	},
};
