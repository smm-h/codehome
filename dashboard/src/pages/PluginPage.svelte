<!--
  Plugin dashboard page.

  Renders a plugin's SDUI layout when available (the plugin response
  includes a `ui` field with an SDUINode tree), or falls back to the
  placeholder UI when no layout has been configured yet.

  Subscribes to plugin SSE events and feeds state updates into the
  pluginState store so that SDUI expressions resolve reactively.
-->
<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import PluginChrome from '$lib/components/PluginChrome.svelte';
	import SDUIRenderer, { type SDUINode } from '$lib/components/SDUIRenderer.svelte';
	import { page } from '$lib/router/router.svelte.js';
	import { pluginStore, type PluginInfo } from '$lib/stores/plugins.svelte.js';
	import { pluginState } from '$lib/stores/pluginState.svelte.js';
	import { preferences } from '$lib/stores/preferences.svelte.js';
	import { sse } from '$lib/stores/sse.svelte.js';
	import { toast as toastStore } from '$lib/stores/toast.svelte.js';
	import { onDestroy } from 'svelte';

	// ---- Plugin resolution from URL ----

	// TODO(plugin-v2): this URL parsing is duplicated in the `plugin`
	// derivation below -- derive pluginName from `plugin` object instead
	// The plugin name is embedded in the URL. For root plugins it's the
	// first path segment after the route prefix; for branch plugins it's
	// the segment after /branch/:repo/:branch/.
	const pluginName = $derived.by(() => {
		const path = page.url.pathname;
		const segments = path.split('/').filter(Boolean);

		// Branch plugin: /branch/:repo/:branch/plugin-route
		if (segments[0] === 'branch' && segments.length >= 4) {
			const routeSegment = segments[3];
			return resolvePluginName(routeSegment);
		}

		// Root plugin: /plugin-route
		if (segments.length >= 1) {
			return resolvePluginName(segments[0]);
		}

		return 'Unknown Plugin';
	});

	/** Look up the human-readable plugin label from the store, falling
	 *  back to the route segment itself. */
	function resolvePluginName(routeSegment: string): string {
		const plugins = pluginStore.withDashboard();
		for (const p of plugins) {
			if (!p.dashboard) continue;
			// Match against the route (strip leading slash for comparison)
			const declaredRoute = p.dashboard.route.replace(/^\//, '');
			if (declaredRoute === routeSegment) {
				return p.dashboard.label || p.name;
			}
		}
		return routeSegment;
	}

	const plugin: PluginInfo | null = $derived.by(() => {
		const path = page.url.pathname;
		const segments = path.split('/').filter(Boolean);
		const routeSegment =
			segments[0] === 'branch' && segments.length >= 4 ? segments[3] : segments[0];
		if (!routeSegment) return null;
		return (
			pluginStore.withDashboard().find((p) => {
				if (!p.dashboard) return false;
				const declaredRoute = p.dashboard.route.replace(/^\//, '');
				return declaredRoute === routeSegment;
			}) ?? null
		);
	});

	// ---- SDUI tree and state ----

	// The plugin's UI tree, fetched from the plugin response.
	// Currently no plugins ship ui.json, so this will be null and the
	// placeholder is shown. When the backend starts returning a `ui`
	// field in GET /api/plugins/{name}, this will pick it up.
	let uiTree = $state<SDUINode | null>(null);
	let uiLoading = $state(false);
	let uiError = $state<string | null>(null);

	// TODO(plugin-v2): add AbortController to cancel stale fetches on rapid
	// navigation between plugins (race condition when multiple plugins serve UI)
	// Fetch plugin detail (including optional `ui` field) when plugin changes
	$effect(() => {
		const p = plugin;
		if (!p) return;

		uiLoading = true;
		uiError = null;
		uiTree = null;

		fetch(`/api/plugins/${encodeURIComponent(p.name)}`)
			.then((resp) => {
				if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
				return resp.json();
			})
			.then((data: Record<string, unknown>) => {
				// If the response includes a `ui` field, use it as the SDUI tree
				if (data.ui && typeof data.ui === 'object') {
					uiTree = data.ui as SDUINode;
				}
				// If the response includes initial `state`, seed the store
				if (data.state && typeof data.state === 'object') {
					pluginState.set(p.name, data.state as Record<string, unknown>);
				}
			})
			.catch((e: unknown) => {
				// 404 is expected -- the detail endpoint may not exist yet.
				// Only surface unexpected errors.
				if (!(e instanceof Error && e.message.includes('404'))) {
					uiError = e instanceof Error ? e.message : String(e);
				}
			})
			.finally(() => {
				uiLoading = false;
			});
	});

	// ---- SSE subscription for plugin state updates ----

	let unsubscribers: (() => void)[] = [];

	// Subscribe to plugin-declared SSE event types and feed updates
	// into the pluginState store.
	$effect(() => {
		// Clean up previous subscriptions
		for (const unsub of unsubscribers) unsub();
		unsubscribers = [];

		const p = plugin;
		if (!p?.dashboard?.event_types) return;

		for (const eventType of p.dashboard.event_types) {
			const unsub = sse.subscribe(eventType, (data) => {
				// SSE events can carry a `state` field with partial updates,
				// or the entire payload is treated as a state update.
				if (data.state && typeof data.state === 'object') {
					pluginState.update(p.name, data.state as Record<string, unknown>);
				} else {
					// Store the event payload keyed by the event type suffix
					// (e.g. "myplugin:status" -> key "status")
					const key = eventType.includes(':') ? (eventType.split(':').pop() as string) : eventType;
					pluginState.update(p.name, { [key]: data });
				}
			});
			unsubscribers.push(unsub);
		}
	});

	onDestroy(() => {
		for (const unsub of unsubscribers) unsub();
		unsubscribers = [];
	});

	// ---- Command handler for SDUI buttons ----

	// Streaming command types from the backend SSE protocol.
	interface CommandProgress {
		type: 'progress';
		message: string;
	}
	interface CommandResult {
		type: 'result';
		toast?: string | null;
		modal?: Record<string, unknown> | null;
		navigate?: string | null;
		state?: Record<string, unknown> | null;
	}
	interface CommandErrorMsg {
		type: 'error';
		message: string;
		detail?: string | null;
	}
	type CommandMessage = CommandProgress | CommandResult | CommandErrorMsg;

	function handleCommand(name: string, params: Record<string, unknown>) {
		if (!plugin) return;

		const pluginName = plugin.name;

		// "input:change" is a local-only synthetic command -- not dispatched
		// to the backend.  Forward to the legacy single-shot endpoint.
		if (name === 'input:change') {
			fetch(`/api/plugins/${encodeURIComponent(pluginName)}/command`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ command: name, params }),
			}).catch((e: unknown) => {
				console.warn(`[PluginPage] command "${name}" failed:`, e);
			});
			return;
		}

		// Stream command via new SSE endpoint.
		streamCommand(pluginName, name, params);
	}

	/**
	 * POST to the streaming command endpoint and process the SSE response.
	 * Falls back to the legacy single-shot endpoint on 404 (command not
	 * registered as a streaming service).
	 */
	async function streamCommand(
		pluginName: string,
		command: string,
		params: Record<string, unknown>,
	) {
		try {
			const resp = await fetch(
				`/api/plugins/${encodeURIComponent(pluginName)}/commands/${encodeURIComponent(command)}`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(params),
				},
			);

			// Fall back to legacy endpoint if the streaming command is not found.
			if (resp.status === 404) {
				await fetch(`/api/plugins/${encodeURIComponent(pluginName)}/command`, {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ command, params }),
				});
				return;
			}

			if (!resp.ok) {
				toastStore.error(`Command failed: HTTP ${resp.status}`);
				return;
			}

			// Read SSE stream from the response body.
			const reader = resp.body?.getReader();
			if (!reader) return;

			const decoder = new TextDecoder();
			let buffer = '';

			while (true) {
				const { done, value } = await reader.read();
				if (done) break;

				buffer += decoder.decode(value, { stream: true });

				// SSE frames are delimited by double newlines.
				let boundary: number;
				while ((boundary = buffer.indexOf('\n\n')) !== -1) {
					const frame = buffer.slice(0, boundary);
					buffer = buffer.slice(boundary + 2);

					// Extract the data payload from the SSE frame.
					const dataLine = frame.split('\n').find((l) => l.startsWith('data: '));
					if (!dataLine) continue;

					const json = dataLine.slice(6); // strip "data: "
					let msg: CommandMessage;
					try {
						msg = JSON.parse(json) as CommandMessage;
					} catch {
						continue;
					}

					handleCommandMessage(pluginName, msg);
				}
			}
		} catch (e: unknown) {
			console.warn(`[PluginPage] streaming command "${command}" failed:`, e);
			toastStore.error(e instanceof Error ? e.message : String(e));
		}
	}

	/**
	 * Process a single message from a streaming command response.
	 */
	function handleCommandMessage(pluginName: string, msg: CommandMessage) {
		switch (msg.type) {
			case 'progress':
				// Merge progress message into plugin state so SDUI expressions
				// can display it (e.g. ${state._progress}).
				pluginState.update(pluginName, { _progress: msg.message });
				break;
			case 'result':
				// Clear progress indicator.
				pluginState.update(pluginName, { _progress: null });
				// Merge any state updates from the result.
				if (msg.state) {
					pluginState.update(pluginName, msg.state);
				}
				// Show toast notification if provided.
				if (msg.toast) {
					toastStore.success(msg.toast);
				}
				// Navigate if requested.
				if (msg.navigate) {
					window.location.href = msg.navigate;
				}
				break;
			case 'error':
				// Clear progress indicator.
				pluginState.update(pluginName, { _progress: null });
				toastStore.error(msg.detail ? `${msg.message}: ${msg.detail}` : msg.message);
				break;
		}
	}

	// Reactive state for the current plugin
	const currentState = $derived(plugin ? pluginState.get(plugin.name) : {});
	const pluginIcon = $derived(plugin?.dashboard?.icon || 'puzzle');
	const useChrome = $derived(preferences.pluginDisplayMode === 'chrome');
</script>

{#snippet content()}
	<div class="plugin-page">
		<div class="plugin-header">
			<Icon name={pluginIcon} size={28} />
			<h1>{pluginName}</h1>
		</div>

		{#if plugin}
			<p class="plugin-description">{plugin.description}</p>
			<p class="plugin-version">v{plugin.version}</p>
		{/if}

		{#if uiLoading}
			<div class="loading-placeholder">
				<div class="skeleton-card" style="height: 120px;"></div>
			</div>
		{:else if uiTree}
			<div class="plugin-ui">
				<SDUIRenderer node={uiTree} state={currentState} onCommand={handleCommand} />
			</div>
		{:else}
			{#if uiError}
				<div class="plugin-error">
					<Icon name="alert-circle" size={20} />
					<span>Failed to load plugin UI: {uiError}</span>
				</div>
			{/if}
			<div class="plugin-placeholder">
				<Icon name="puzzle" size={48} />
				<p>Plugin UI not yet configured</p>
				<p class="plugin-hint">
					This plugin has registered a dashboard route but does not yet provide a custom layout.
				</p>
			</div>
		{/if}
	</div>
{/snippet}

{#if useChrome}
	<PluginChrome {pluginName} {pluginIcon}>
		{@render content()}
	</PluginChrome>
{:else}
	{@render content()}
{/if}

<style>
	.plugin-page {
		max-width: 800px;
		margin: 0 auto;
		padding: 32px 16px;
	}

	.plugin-header {
		display: flex;
		align-items: center;
		gap: 12px;
		margin-bottom: 8px;
	}

	.plugin-header h1 {
		margin: 0;
		font-size: 22px;
		font-weight: 600;
		color: var(--text);
	}

	.plugin-description {
		color: var(--text-muted);
		font-size: 14px;
		margin: 0 0 4px;
	}

	.plugin-version {
		color: var(--text-dim);
		font-size: 12px;
		margin: 0 0 32px;
	}

	.plugin-ui {
		margin-top: 8px;
	}

	.plugin-error {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 10px 14px;
		margin-bottom: 16px;
		background: rgba(239, 68, 68, 0.1);
		border: 1px solid var(--danger);
		border-radius: var(--radius);
		color: var(--danger);
		font-size: 13px;
	}

	.plugin-placeholder {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 12px;
		padding: 48px 24px;
		border: 2px dashed var(--border);
		border-radius: var(--radius);
		text-align: center;
		color: var(--text-dim);
	}

	.plugin-placeholder p {
		margin: 0;
		font-size: 14px;
	}

	.plugin-hint {
		font-size: 12px !important;
		max-width: 360px;
	}
</style>
