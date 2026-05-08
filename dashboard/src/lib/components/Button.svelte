<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	interface Props extends HTMLButtonAttributes {
		variant?: 'primary' | 'danger' | 'ghost' | 'outline';
		size?: 'sm' | 'md';
		disabled?: boolean;
		loading?: boolean;
		type?: 'button' | 'submit';
		icon?: Snippet;
		children?: Snippet;
		/** Bindable reference to the underlying <button> element. */
		ref?: HTMLButtonElement | null;
	}

	let {
		variant = 'primary',
		size = 'md',
		disabled = false,
		loading = false,
		type = 'button',
		icon,
		children,
		class: className,
		ref = $bindable(null),
		...rest
	}: Props = $props();

	// Button is effectively disabled when loading or explicitly disabled
	const isDisabled = $derived(disabled || loading);
</script>

<button
	bind:this={ref}
	{type}
	class="btn btn-{variant} btn-{size} {className ?? ''}"
	disabled={isDisabled}
	{...rest}
>
	{#if loading}
		<span class="spinner" aria-hidden="true"></span>
	{:else if icon}
		{@render icon()}
	{/if}
	{#if children}{@render children()}{/if}
</button>

<style>
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		font-weight: 500;
		border: none;
		border-radius: var(--radius);
		cursor: pointer;
		transition:
			background 0.15s,
			color 0.15s,
			filter 0.15s,
			border-color 0.15s;
		white-space: nowrap;
	}

	/* ── Sizes ── */
	.btn-sm {
		padding: 4px 10px;
		font-size: 12px;
	}

	.btn-md {
		padding: 6px 12px;
		font-size: 13px;
	}

	/* ── Variants ── */
	.btn-primary {
		background: var(--accent);
		color: var(--bg);
	}

	.btn-primary:hover:not(:disabled) {
		filter: brightness(1.1);
	}

	.btn-danger {
		background: var(--error, var(--danger));
		color: white;
	}

	.btn-danger:hover:not(:disabled) {
		filter: brightness(1.1);
	}

	.btn-ghost {
		background: transparent;
		color: var(--text-muted);
	}

	.btn-ghost:hover:not(:disabled) {
		background: var(--bg-hover);
		color: var(--text);
	}

	.btn-outline {
		background: transparent;
		color: var(--text-muted);
		border: 1px solid var(--border);
	}

	.btn-outline:hover:not(:disabled) {
		background: var(--bg-hover);
		color: var(--text);
		border-color: var(--text-muted);
	}

	/* ── Disabled state ── */
	.btn:disabled {
		opacity: 0.5;
		cursor: default;
	}

	/* ── Loading spinner ── */
	.spinner {
		display: inline-block;
		width: 12px;
		height: 12px;
		border: 2px solid currentColor;
		border-right-color: transparent;
		border-radius: 50%;
		animation: spin 0.6s linear infinite;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
