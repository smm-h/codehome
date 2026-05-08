<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import { getMatch, type MatchedRoute } from './router.svelte.js';

	/**
	 * Router component: resolves the current matched route and renders
	 * the lazy-loaded page component inside the appropriate layout.
	 *
	 * Layouts are provided as snippets by the parent (App.svelte) so
	 * the Router stays decoupled from layout implementations.
	 */
	interface Props {
		/** Renders page inside the root layout. Receives {page, params} as snippet arg. */
		rootLayout: Snippet<[{ page: Component; params: Record<string, string> }]>;
		/** Renders page inside the branch layout (nested in root). Receives {page, params}. */
		branchLayout: Snippet<[{ page: Component; params: Record<string, string> }]>;
		/** Renders page with no layout wrapper. Receives {page, params}. */
		noLayout: Snippet<[{ page: Component; params: Record<string, string> }]>;
	}

	let { rootLayout, branchLayout, noLayout }: Props = $props();

	let match: MatchedRoute | null = $derived(getMatch());
	let loadedComponent = $state<Component | null>(null);
	let loadingPath = $state<string | null>(null);
	let loadError = $state<string | null>(null);
	// The match snapshot that corresponds to `loadedComponent`. Used for
	// rendering the previous page while the next lazy-load is in flight,
	// preventing layout unmount/remount (and the SSE reconnect flash).
	let renderedMatch = $state<MatchedRoute | null>(null);

	// Load the component whenever the matched route changes.
	$effect(() => {
		if (!match) {
			loadedComponent = null;
			loadingPath = null;
			loadError = null;
			renderedMatch = null;
			return;
		}

		const routePath = match.route.path;

		// Avoid reloading the same component if the route pattern hasn't changed
		// (e.g. navigating between /branch/bag/a/git and /branch/bag/b/git).
		if (routePath === loadingPath && loadedComponent) {
			// Same route pattern but params may differ -- update renderedMatch.
			renderedMatch = match;
			return;
		}

		loadingPath = routePath;
		// Keep loadedComponent and renderedMatch as-is so the previous page
		// stays visible while the new component loads asynchronously.
		loadError = null;

		// Capture the match at the time the load started so we can pair it
		// with the resolved component (the derived `match` may change again
		// before the promise settles).
		const pendingMatch = match;

		match.route
			.component()
			.then((mod) => {
				// Guard against stale loads: only update if this is still the current route.
				if (loadingPath === routePath) {
					loadedComponent = mod.default;
					renderedMatch = pendingMatch;
				}
			})
			.catch((err) => {
				if (loadingPath === routePath) {
					loadError = err?.message ?? 'Failed to load page';
				}
			});
	});
</script>

{#if loadError}
	<div class="router-error">
		<p>Failed to load page: {loadError}</p>
	</div>
{:else if loadedComponent && renderedMatch}
	{@const page = loadedComponent}
	{@const params = renderedMatch.params}
	{@const layout = renderedMatch.route.layout}

	{#if layout === 'branch'}
		{@render branchLayout({ page, params })}
	{:else if layout === 'none'}
		{@render noLayout({ page, params })}
	{:else}
		{@render rootLayout({ page, params })}
	{/if}
{/if}

<style>
	.router-error {
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 40px 20px;
		color: var(--text-muted, #888);
		font-family: var(--font, sans-serif);
	}

	.router-error p {
		margin: 0;
	}
</style>
