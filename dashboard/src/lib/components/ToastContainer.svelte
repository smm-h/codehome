<script lang="ts">
	import { SvelteSet } from 'svelte/reactivity';
	import { toast } from '$lib/stores/toast.svelte.js';
	import Button from './Button.svelte';

	let expandedIds = new SvelteSet<number>();

	function toggleDetails(e: MouseEvent, id: number) {
		e.stopPropagation();
		if (expandedIds.has(id)) {
			expandedIds.delete(id);
		} else {
			expandedIds.add(id);
		}
	}
</script>

{#if toast.toasts.length > 0}
	<div class="toast-container" role="alert" aria-live="assertive">
		{#each toast.toasts as t (t.id)}
			<Button
				variant="ghost"
				size="sm"
				class="toast toast-{t.level}"
				onclick={() => toast.dismiss(t.id)}
				aria-label={t.message}
			>
				<span class="toast-bar"></span>
				<span class="toast-body">
					<span class="toast-message">{t.message}</span>
					{#if t.details}
						<button class="toast-details-toggle" onclick={(e) => toggleDetails(e, t.id)}>
							{expandedIds.has(t.id) ? 'Hide details' : 'Details'}
						</button>
						{#if expandedIds.has(t.id)}
							<span class="toast-details">{t.details}</span>
						{/if}
					{/if}
				</span>
				<span class="toast-dismiss" aria-hidden="true">x</span>
			</Button>
		{/each}
	</div>
{/if}

<style>
	.toast-container {
		position: fixed;
		top: 12px;
		right: 12px;
		z-index: 9999;
		display: flex;
		flex-direction: column;
		gap: 8px;
		max-width: 380px;
		width: 100%;
		pointer-events: none;
	}

	/* Center on mobile */
	@media (max-width: 768px) {
		.toast-container {
			right: 50%;
			transform: translateX(50%);
			max-width: calc(100vw - 24px);
		}
	}

	.toast {
		display: flex;
		align-items: center;
		gap: 10px;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 10px 12px;
		color: var(--text);
		font-size: 13px;
		text-align: left;
		pointer-events: auto;
		cursor: pointer;
		overflow: hidden;
		animation: toast-slide-in 0.2s ease-out;
	}

	.toast:hover {
		background: var(--bg-hover);
	}

	@keyframes toast-slide-in {
		from {
			opacity: 0;
			transform: translateX(20px);
		}
		to {
			opacity: 1;
			transform: translateX(0);
		}
	}

	@media (max-width: 768px) {
		@keyframes toast-slide-in {
			from {
				opacity: 0;
				transform: translateY(-10px);
			}
			to {
				opacity: 1;
				transform: translateY(0);
			}
		}
	}

	.toast-bar {
		width: 3px;
		min-height: 20px;
		align-self: stretch;
		border-radius: 2px;
		flex-shrink: 0;
	}

	.toast-error .toast-bar {
		background: var(--danger);
	}

	.toast-warning .toast-bar {
		background: var(--warning);
	}

	.toast-info .toast-bar {
		background: var(--accent);
	}

	.toast-success .toast-bar {
		background: var(--success);
	}

	.toast-body {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}

	.toast-message {
		line-height: 1.4;
	}

	.toast-details-toggle {
		all: unset;
		font-size: 11px;
		color: var(--text-dim);
		cursor: pointer;
		text-decoration: underline;
		text-decoration-style: dotted;
		align-self: flex-start;
	}

	.toast-details-toggle:hover {
		color: var(--text-muted);
	}

	.toast-details {
		font-size: 11px;
		color: var(--text-dim);
		line-height: 1.3;
		word-break: break-word;
	}

	.toast-dismiss {
		color: var(--text-dim);
		font-size: 14px;
		flex-shrink: 0;
		font-family: var(--font-mono);
	}
</style>
