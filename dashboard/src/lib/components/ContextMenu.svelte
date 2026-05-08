<script lang="ts">
	import { contextMenu } from '$lib/stores/contextMenu.svelte.js';
	import { toast } from '$lib/stores/toast.svelte.js';
	import Icon from './Icon.svelte';
	import Button from './Button.svelte';

	let menuEl = $state<HTMLDivElement | null>(null);
	// Index of the currently focused item for keyboard navigation (-1 = none).
	let focusIndex = $state(-1);

	// Actionable (non-separator, non-disabled) item indices for keyboard nav.
	const actionableIndices = $derived(
		contextMenu.items
			.map((item, i) => ({ item, i }))
			.filter(({ item }) => !item.separator && !item.disabled)
			.map(({ i }) => i),
	);

	// Adjusted position to keep menu within viewport.
	let adjustedX = $state(0);
	let adjustedY = $state(0);

	// Recompute position when the menu opens or the element mounts.
	$effect(() => {
		if (!contextMenu.open || !menuEl) return;

		// Start at the raw cursor position to avoid a flash at (0,0).
		adjustedX = contextMenu.x;
		adjustedY = contextMenu.y;

		// Wait one frame for the menu to render and have dimensions,
		// then refine position to keep it within the viewport.
		requestAnimationFrame(() => {
			if (!menuEl) return;
			const rect = menuEl.getBoundingClientRect();
			const vw = window.innerWidth;
			const vh = window.innerHeight;
			const margin = 8;

			let x = contextMenu.x;
			let y = contextMenu.y;

			// Flip left if too close to right edge.
			if (x + rect.width + margin > vw) {
				x = Math.max(margin, x - rect.width);
			}
			// Flip up if too close to bottom.
			if (y + rect.height + margin > vh) {
				y = Math.max(margin, y - rect.height);
			}

			adjustedX = x;
			adjustedY = y;
		});
	});

	// Reset focus index when menu opens.
	$effect(() => {
		if (contextMenu.open) {
			focusIndex = -1;
			// Focus the menu container for keyboard events.
			requestAnimationFrame(() => menuEl?.focus());
		}
	});

	// Close on scroll, resize, or outside click.
	$effect(() => {
		if (!contextMenu.open) return;

		function onClose() {
			contextMenu.hideMenu();
		}

		function onClickOutside(e: MouseEvent) {
			if (menuEl && !menuEl.contains(e.target as Node)) {
				contextMenu.hideMenu();
			}
		}

		// Use capture for scroll so we catch scroll on any element.
		window.addEventListener('scroll', onClose, true);
		window.addEventListener('resize', onClose);
		document.addEventListener('mousedown', onClickOutside);

		return () => {
			window.removeEventListener('scroll', onClose, true);
			window.removeEventListener('resize', onClose);
			document.removeEventListener('mousedown', onClickOutside);
		};
	});

	function selectItem(index: number) {
		const item = contextMenu.items[index];
		if (!item || item.separator || item.disabled) return;
		contextMenu.hideMenu();
		item.action();
	}

	function onKeydown(e: KeyboardEvent) {
		if (!contextMenu.open) return;

		if (e.key === 'Escape') {
			e.preventDefault();
			contextMenu.hideMenu();
			return;
		}

		if (e.key === 'ArrowDown') {
			e.preventDefault();
			if (actionableIndices.length === 0) return;
			const currentPos = actionableIndices.indexOf(focusIndex);
			const nextPos = currentPos < actionableIndices.length - 1 ? currentPos + 1 : 0;
			focusIndex = actionableIndices[nextPos];
			return;
		}

		if (e.key === 'ArrowUp') {
			e.preventDefault();
			if (actionableIndices.length === 0) return;
			const currentPos = actionableIndices.indexOf(focusIndex);
			const prevPos = currentPos > 0 ? currentPos - 1 : actionableIndices.length - 1;
			focusIndex = actionableIndices[prevPos];
			return;
		}

		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			if (focusIndex >= 0) {
				selectItem(focusIndex);
			}
			return;
		}
	}

	// Fallback menu items shown when right-clicking an area with no registered items.
	const FALLBACK_ITEMS = [
		{
			label: 'Refresh',
			icon: 'refresh',
			action: () => window.location.reload(),
		},
		{
			label: 'New Branch',
			icon: 'plus',
			action: () => toast.info('Coming soon'),
		},
	];

	// Install a global contextmenu handler for the fallback menu.
	$effect(() => {
		if (typeof window === 'undefined') return;

		function onGlobalContext(e: MouseEvent) {
			// Only fire if no other handler already showed a context menu.
			// The contextItems action calls stopPropagation, so this only
			// fires for areas without registered items.
			e.preventDefault();
			contextMenu.showMenu(e.clientX, e.clientY, FALLBACK_ITEMS);
		}

		window.addEventListener('contextmenu', onGlobalContext);
		return () => window.removeEventListener('contextmenu', onGlobalContext);
	});
</script>

{#if contextMenu.open}
	<div
		class="context-menu"
		style="left: {adjustedX}px; top: {adjustedY}px"
		bind:this={menuEl}
		onkeydown={onKeydown}
		role="menu"
		tabindex="-1"
	>
		{#each contextMenu.items as item, i (i)}
			{#if item.separator}
				<div class="context-separator" role="separator"></div>
			{:else}
				<Button
					variant="ghost"
					size="sm"
					class="context-item {item.danger ? 'context-item-danger' : ''} {item.disabled ? 'context-item-disabled' : ''} {focusIndex === i ? 'context-item-focused' : ''}"
					role="menuitem"
					tabindex={-1}
					disabled={item.disabled}
					onclick={() => selectItem(i)}
					onmouseenter={() => {
						focusIndex = i;
					}}
				>
					{#if item.icon}
						<span class="context-icon">
							<Icon name={item.icon} size={14} />
						</span>
					{/if}
					<span class="context-label">{item.label}</span>
				</Button>
			{/if}
		{/each}
	</div>
{/if}

<style>
	.context-menu {
		position: fixed;
		z-index: 10001;
		min-width: 180px;
		max-width: 280px;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow:
			0 8px 24px rgba(0, 0, 0, 0.35),
			0 2px 8px rgba(0, 0, 0, 0.15);
		padding: 4px 0;
		outline: none;
		animation: ctx-fade-in 0.1s ease-out;
	}

	@keyframes ctx-fade-in {
		from {
			opacity: 0;
			transform: scale(0.96);
		}
		to {
			opacity: 1;
			transform: scale(1);
		}
	}

	.context-separator {
		height: 1px;
		background: var(--border);
		margin: 4px 8px;
	}

	.context-item {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		padding: 6px 12px;
		font-size: 12px;
		color: var(--text);
		text-align: left;
		border: none;
		background: none;
		cursor: pointer;
		transition: background 0.1s;
		/* Override global mobile min-height for compact menu items */
		min-height: unset;
		min-width: unset;
	}

	.context-item:hover,
	.context-item-focused {
		background: var(--bg-hover);
	}

	.context-item-danger {
		color: var(--danger);
	}

	.context-item-danger:hover,
	.context-item-danger.context-item-focused {
		background: color-mix(in srgb, var(--danger) 10%, transparent);
	}

	.context-item-disabled {
		color: var(--text-dim);
		opacity: 0.5;
		cursor: not-allowed;
	}

	.context-item-disabled:hover,
	.context-item-disabled.context-item-focused {
		background: transparent;
	}

	.context-icon {
		display: flex;
		align-items: center;
		flex-shrink: 0;
		color: inherit;
		opacity: 0.7;
	}

	.context-label {
		flex: 1;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
