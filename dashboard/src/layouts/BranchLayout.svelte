<script lang="ts">
	/**
	 * Branch layout shell.
	 *
	 * Provides breadcrumb header, tab bar, rename dialog, and recent-branch
	 * tracking for branch sub-pages. Receives `params` from the router
	 * (contains `repo` and `branch`).
	 */
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { i18n } from '$lib/i18n/index.svelte.js';
	import { api } from '$lib/api.js';
	import { allTabs } from '$lib/tabs.js';
	import { features } from '$lib/stores/features.svelte.js';
	import { qualifiedFromParams, branchUrl } from '$lib/url.js';
	import TabBar from '$lib/components/TabBar.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Button from '$lib/components/Button.svelte';
	import RenameDialog from '$lib/components/RenameDialog.svelte';
	import { goto, page } from '$lib/router/router.svelte.js';
	import { reportError } from '$lib/errors';

	interface Props {
		params: Record<string, string>;
		children: Snippet;
	}

	let { params, children }: Props = $props();

	let renameDialogRef = $state<ReturnType<typeof RenameDialog> | null>(null);

	// Reconstruct the qualified name ("repo:branch") from the two URL params
	const qualified = $derived(qualifiedFromParams(params.repo ?? '', params.branch ?? ''));

	/** Record this branch as recently accessed in user preferences.
	 *  Keeps the 10 most recent entries (deduped by qualified name). */
	async function recordRecentBranch(name: string) {
		try {
			const raw = await api.get('/api/preferences/recent-branches').catch((e: unknown) => {
				reportError(e, { silent: true, category: 'preferences' });
				return null;
			});
			// The API returns {} when no data is stored yet -- treat non-arrays as empty.
			const current: { qualified: string; timestamp: number }[] = Array.isArray(raw) ? raw : [];
			const now = Date.now();
			// Remove any existing entry for this branch, then prepend.
			const filtered = current.filter((e) => e.qualified !== name);
			const updated = [{ qualified: name, timestamp: now }, ...filtered].slice(0, 10);
			await api.put('/api/preferences/recent-branches', updated);
		} catch (e) {
			reportError(e, { silent: true, category: 'preferences' });
		}
	}

	onMount(() => {
		recordRecentBranch(qualified);
	});

	// The branch short name is just the branch param directly
	const branchDisplay = $derived(params.branch ?? '');

	// Filter tabs: branch-scoped tabs stay in branch context, gated by feature flags.
	// Uses allTabs() to include plugin-contributed branch tabs.
	const branchTabs = $derived(
		allTabs().filter((t) => t.scope === 'branch' && (!t.flag || features.enabled(t.flag))),
	);

	// Determine which tab is active based on the current path segment after /branch/[repo]/[branch]/.
	// Handles both core tabs (key === segment) and plugin tabs (key === "plugin:<segment>").
	const activeTab = $derived.by(() => {
		const path = page.url.pathname;
		const segments = path.split('/');
		// segments: ['', 'branch', repo, branch, tab?, ...]
		const tabSegment = segments[4];
		if (tabSegment) {
			// Direct match for core tabs
			const matched = branchTabs.find((t) => t.key === tabSegment);
			if (matched) return matched.key;
			// Plugin tabs: key is "plugin:<name>", route segment is just <name>
			const pluginMatch = branchTabs.find(
				(t) => t.key.startsWith('plugin:') && t.key.slice('plugin:'.length) === tabSegment,
			);
			if (pluginMatch) return pluginMatch.key;
		}
		return '';
	});

	function selectTab(key: string) {
		// Plugin tabs use keys like "plugin:<name>" -- extract the route
		// segment for URL construction.
		const routeSegment = key.startsWith('plugin:') ? key.slice('plugin:'.length) : key;
		goto(branchUrl(qualified, routeSegment));
	}

	function navigateToOverview() {
		goto('/branches');
	}

	function handleRenamed(newQualified: string) {
		// Navigate to the new branch URL, replacing the current history entry.
		goto(branchUrl(newQualified), { replaceState: true });
	}
</script>

<div class="branch-layout">
	<div class="branch-header">
		<Button
			variant="ghost"
			size="sm"
			class="breadcrumb-back"
			onclick={navigateToOverview}
			aria-label={i18n.t('nav.overview')}
			title={i18n.t('nav.overview')}
		>
			<Icon name="arrow-left" size={16} />
			<span class="breadcrumb-overview">{i18n.t('nav.overview')}</span>
		</Button>
		<span class="breadcrumb-sep">/</span>
		<span class="breadcrumb-branch">{branchDisplay}</span>
		<Button
			variant="ghost"
			size="sm"
			class="breadcrumb-rename"
			onclick={() => renameDialogRef?.show()}
			aria-label={i18n.t('branch.rename')}
			title={i18n.t('branch.rename')}
		>
			<Icon name="pencil" size={13} />
		</Button>
	</div>

	<div class="branch-tabs">
		<TabBar tabs={branchTabs} {activeTab} onSelect={selectTab} />
	</div>

	<div class="branch-content">
		{@render children()}
	</div>
</div>

<RenameDialog bind:this={renameDialogRef} {qualified} onRenamed={handleRenamed} />

<style>
	.branch-layout {
		display: flex;
		flex-direction: column;
		height: 100%;
	}

	.branch-header {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 8px 16px;
		font-size: 13px;
		border-bottom: 1px solid var(--border);
		background: var(--bg-surface);
		flex-shrink: 0;
	}

	.breadcrumb-back {
		display: flex;
		align-items: center;
		gap: 4px;
		color: var(--text-muted);
		padding: 2px 6px;
		border-radius: var(--radius);
		transition:
			color 0.15s,
			background 0.15s;
	}

	.breadcrumb-back:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.breadcrumb-overview {
		font-weight: 500;
	}

	.breadcrumb-sep {
		color: var(--text-dim);
	}

	.breadcrumb-branch {
		font-weight: 600;
		color: var(--text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.breadcrumb-rename {
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 2px;
		color: var(--text-dim);
		border-radius: var(--radius);
		opacity: 0.5;
		transition:
			opacity 0.15s,
			color 0.15s,
			background 0.15s;
	}

	.breadcrumb-rename:hover {
		opacity: 1;
		color: var(--text);
		background: var(--bg-hover);
	}

	.branch-tabs {
		flex-shrink: 0;
		overflow: hidden;
	}

	/* Remove the bottom border from the tab bar since the content area
	   provides its own visual separation. */
	.branch-tabs :global(.tab-bar) {
		border-bottom: 1px solid var(--border);
	}

	.branch-content {
		flex: 1;
		overflow-y: auto;
		padding: 16px;
	}
</style>
