<script lang="ts">
	import type { NavLayoutProps } from './types';
	import Icon from '$lib/components/Icon.svelte';

	let { items, activeItem, onNavigate }: NavLayoutProps = $props();
</script>

<nav class="nav-second-row" aria-label="Navigation">
	<div class="page-row">
		{#each items as item (item.key)}
			<button
				class="page-btn"
				class:active={item.key === activeItem}
				onclick={() => onNavigate(item.path)}
				aria-current={item.key === activeItem ? 'page' : undefined}
			>
				<Icon name={item.icon} size={14} />
				<span class="page-label">{item.label}</span>
			</button>
		{/each}
	</div>
</nav>

<style>
	.nav-second-row {
		display: flex;
		flex-direction: column;
		background: var(--bg-surface);
	}

	.page-row {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 8px;
		height: 36px;
		border-bottom: 1px solid var(--border);
		overflow-x: auto;
	}

	.page-row::-webkit-scrollbar {
		display: none;
	}
	.page-row {
		scrollbar-width: none;
	}

	.page-btn {
		display: flex;
		align-items: center;
		gap: 5px;
		padding: 4px 10px;
		border: none;
		border-radius: var(--radius);
		background: transparent;
		color: var(--text-muted);
		font-size: 12px;
		font-weight: 500;
		font-family: var(--font);
		cursor: pointer;
		white-space: nowrap;
		transition: color 0.15s, background 0.15s;
	}

	.page-btn:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.page-btn.active {
		color: var(--accent);
		background: var(--bg-hover);
	}

	.page-label {
		line-height: 1;
	}
</style>
