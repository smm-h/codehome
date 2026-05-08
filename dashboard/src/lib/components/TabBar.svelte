<script lang="ts">
	import Icon from './Icon.svelte';
	import Button from './Button.svelte';
	import { i18n } from '$lib/i18n/index.svelte.js';
	import type { Tab } from '$lib/tabs.js';

	interface Props {
		tabs: readonly Tab[];
		activeTab: string;
		onSelect: (key: string) => void;
	}

	let { tabs, activeTab, onSelect }: Props = $props();
</script>

<nav class="tab-bar" aria-label="Tab navigation">
	{#each tabs as tab (tab.key)}
		<Button
			variant="ghost"
			size="sm"
			class="tab-item {activeTab === tab.key ? 'active' : ''}"
			aria-current={activeTab === tab.key ? 'page' : undefined}
			aria-label={i18n.t(tab.i18n)}
			title={i18n.t(tab.i18n)}
			onclick={() => onSelect(tab.key)}
		>
			<Icon name={tab.icon} size={18} />
			<span class="tab-label">{i18n.t(tab.i18n)}</span>
		</Button>
	{/each}
</nav>

<style>
	.tab-bar {
		display: flex;
		align-items: center;
		gap: 2px;
		background: var(--bg-surface);
		border-bottom: 1px solid var(--border);
		padding: 0 8px;
		height: 42px;
		overflow-x: auto;
		flex-shrink: 0;
	}

	/* Hide scrollbar on the tab bar */
	.tab-bar::-webkit-scrollbar {
		display: none;
	}
	.tab-bar {
		scrollbar-width: none;
	}

	.tab-item {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 6px 10px;
		border-radius: var(--radius);
		color: var(--text-muted);
		white-space: nowrap;
		transition:
			color 0.15s,
			background 0.15s;
	}

	.tab-item:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.tab-item.active {
		color: var(--accent);
		background: var(--bg-hover);
	}

	.tab-label {
		font-size: 12px;
		font-weight: 500;
	}
</style>
