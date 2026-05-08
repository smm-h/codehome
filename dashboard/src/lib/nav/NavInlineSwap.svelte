<script lang="ts">
	import type { NavLayoutProps } from './types';
	import Icon from '$lib/components/Icon.svelte';

	let { items, activeItem, onNavigate }: NavLayoutProps = $props();

	let overflowOpen = $state(false);
	let visibleCount = $state(items.length);
	let rowEl = $state<HTMLElement | null>(null);

	function recalcOverflow() {
		if (!rowEl) return;
		const containerWidth = rowEl.clientWidth;
		const children = Array.from(rowEl.children) as HTMLElement[];
		let usedWidth = 0;
		let count = 0;
		const overflowBtnWidth = 40;

		for (const child of children) {
			if (child.classList.contains('overflow-wrapper')) continue;
			const w = child.offsetWidth + 4;
			if (usedWidth + w + overflowBtnWidth > containerWidth && count < items.length) {
				break;
			}
			usedWidth += w;
			count++;
		}

		visibleCount = count === children.filter((c) => !c.classList.contains('overflow-wrapper')).length
			? items.length
			: count;
	}

	$effect(() => {
		items;
		recalcOverflow();
	});

	const visibleItems = $derived(items.slice(0, visibleCount));
	const overflowItems = $derived(items.slice(visibleCount));

	function selectItem(path: string) {
		onNavigate(path);
		overflowOpen = false;
	}

	function handleClickOutside(event: MouseEvent) {
		const target = event.target as HTMLElement;
		if (!target.closest('.nav-inline-swap')) {
			overflowOpen = false;
		}
	}
</script>

<svelte:window onclick={handleClickOutside} onresize={recalcOverflow} />

<nav class="nav-inline-swap" aria-label="Navigation">
	<div class="row" bind:this={rowEl}>
		{#each visibleItems as item (item.key)}
			<button
				class="page-btn"
				class:active={item.key === activeItem}
				onclick={() => selectItem(item.path)}
				aria-current={item.key === activeItem ? 'page' : undefined}
			>
				<Icon name={item.icon} size={14} />
				<span class="page-label">{item.label}</span>
			</button>
		{/each}
		{#if overflowItems.length > 0}
			<div class="overflow-wrapper">
				<button
					class="overflow-btn"
					onclick={(e) => { e.stopPropagation(); overflowOpen = !overflowOpen; }}
					aria-expanded={overflowOpen}
					aria-haspopup="menu"
				>
					<Icon name="more-horizontal" size={14} />
				</button>
				{#if overflowOpen}
					<div class="dropdown-menu" role="menu">
						{#each overflowItems as item (item.key)}
							<button
								class="dropdown-item"
								class:active={item.key === activeItem}
								onclick={(e) => { e.stopPropagation(); selectItem(item.path); }}
								role="menuitem"
							>
								<Icon name={item.icon} size={14} />
								<span class="dropdown-label">{item.label}</span>
								{#if item.key === activeItem}
									<span class="active-dot"></span>
								{/if}
							</button>
						{/each}
					</div>
				{/if}
			</div>
		{/if}
	</div>
</nav>

<style>
	.nav-inline-swap {
		background: var(--bg-surface);
	}

	.row {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 0 8px;
		height: 42px;
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

	.overflow-wrapper {
		position: relative;
		margin-left: auto;
	}

	.overflow-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 28px;
		border: none;
		border-radius: var(--radius);
		background: transparent;
		color: var(--text-muted);
		cursor: pointer;
		transition: color 0.15s, background 0.15s;
	}

	.overflow-btn:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.dropdown-menu {
		position: absolute;
		top: 100%;
		right: 0;
		margin-top: 4px;
		min-width: 160px;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
		padding: 4px;
		z-index: 100;
	}

	.dropdown-item {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		padding: 6px 10px;
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

	.dropdown-item:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.dropdown-item.active {
		color: var(--accent);
	}

	.dropdown-label {
		flex: 1;
	}

	.active-dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--accent);
		flex-shrink: 0;
	}

	.page-label {
		line-height: 1;
	}
</style>
