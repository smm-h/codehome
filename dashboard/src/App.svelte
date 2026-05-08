<script lang="ts">
	import type { Component } from 'svelte';
	import Router from './lib/router/Router.svelte';
	import RootLayout from './layouts/RootLayout.svelte';
	import BranchLayout from './layouts/BranchLayout.svelte';
	import { auth } from '$lib/stores/auth.svelte.js';
	import { sse } from '$lib/stores/sse.svelte.js';
	import { pluginStore } from '$lib/stores/plugins.svelte.js';
	import { injectPluginRoutes } from '$lib/router/routes.js';
	import { registerPluginTabs } from '$lib/tabs.js';

	// SSE lifecycle is managed here (app-level singleton) so it survives
	// route transitions. RootLayout used to own this, but it unmounts
	// during async page loads, causing a disconnect/reconnect flash.
	$effect(() => {
		if (!auth.isAuthenticated) return;

		const token =
			typeof sessionStorage !== 'undefined'
				? (sessionStorage.getItem('auth-token') ?? undefined)
				: undefined;
		sse.connect(token);

		// Load plugin metadata, then wire up routes, tabs, and SSE event types.
		// This runs once after authentication; plugins don't change at runtime.
		pluginStore.load().then(() => {
			const dashboardPlugins = pluginStore.withDashboard();
			if (dashboardPlugins.length > 0) {
				injectPluginRoutes(pluginStore.plugins);
				registerPluginTabs(pluginStore.tabs());
				sse.registerEventTypes(pluginStore.eventTypes());
			}
		});

		return () => sse.disconnect();
	});
</script>

<Router>
	{#snippet rootLayout(ctx: { page: Component; params: Record<string, string> })}
		{@const Page = ctx.page}
		<RootLayout>
			<Page />
		</RootLayout>
	{/snippet}

	{#snippet branchLayout(ctx: { page: Component; params: Record<string, string> })}
		{@const Page = ctx.page}
		<RootLayout>
			<BranchLayout params={ctx.params}>
				<Page />
			</BranchLayout>
		</RootLayout>
	{/snippet}

	{#snippet noLayout(ctx: { page: Component; params: Record<string, string> })}
		{@const Page = ctx.page}
		<Page />
	{/snippet}
</Router>
