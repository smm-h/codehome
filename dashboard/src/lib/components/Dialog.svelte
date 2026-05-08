<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		open: boolean;
		size?: 'sm' | 'md' | 'lg' | 'fullscreen';
		closeOnBackdrop?: boolean;
		closeOnEscape?: boolean;
		/** Accessible name announced by screen readers (use when there is no visible header) */
		label?: string;
		header?: Snippet;
		footer?: Snippet;
		children: Snippet;
	}

	let {
		open = $bindable(),
		size = 'md',
		closeOnBackdrop = true,
		closeOnEscape = true,
		label,
		header,
		footer,
		children,
	}: Props = $props();

	let dialogEl = $state<HTMLDivElement | null>(null);
	// Element that had focus before the dialog opened, restored on close
	let previouslyFocused: HTMLElement | null = null;

	const FOCUSABLE_SELECTOR =
		'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

	function getFocusableElements(): HTMLElement[] {
		if (!dialogEl) return [];
		return Array.from(dialogEl.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
	}

	function close() {
		open = false;
	}

	// Manage focus and body scroll when open/close state changes
	$effect(() => {
		if (open) {
			previouslyFocused = document.activeElement as HTMLElement | null;
			document.body.style.overflow = 'hidden';
			// Wait for DOM to render, then focus first focusable or the dialog itself
			requestAnimationFrame(() => {
				const focusable = getFocusableElements();
				if (focusable.length > 0) {
					focusable[0].focus();
				} else {
					dialogEl?.focus();
				}
			});
		} else {
			document.body.style.overflow = '';
			if (previouslyFocused) {
				previouslyFocused.focus();
				previouslyFocused = null;
			}
		}

		// Cleanup: restore body scroll if component is destroyed while open
		return () => {
			document.body.style.overflow = '';
		};
	});

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && closeOnEscape) {
			e.preventDefault();
			close();
			return;
		}

		// Focus trap: cycle Tab / Shift+Tab within the dialog
		if (e.key === 'Tab') {
			const focusable = getFocusableElements();
			if (focusable.length === 0) {
				e.preventDefault();
				return;
			}

			const first = focusable[0];
			const last = focusable[focusable.length - 1];

			if (e.shiftKey) {
				if (document.activeElement === first) {
					e.preventDefault();
					last.focus();
				}
			} else {
				if (document.activeElement === last) {
					e.preventDefault();
					first.focus();
				}
			}
		}
	}

	function onBackdropClick(e: MouseEvent) {
		if (closeOnBackdrop && e.target === e.currentTarget) {
			close();
		}
	}
</script>

{#if open}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="dialog-backdrop" onclick={onBackdropClick} onkeydown={onKeydown}>
		<div
			class="dialog-panel dialog-{size}"
			role="dialog"
			aria-modal="true"
			aria-label={label}
			tabindex="-1"
			bind:this={dialogEl}
		>
			{#if header}
				<div class="dialog-header">
					{@render header()}
				</div>
			{/if}

			<div class="dialog-content">
				{@render children()}
			</div>

			{#if footer}
				<div class="dialog-footer">
					{@render footer()}
				</div>
			{/if}
		</div>
	</div>
{/if}

<style>
	.dialog-backdrop {
		position: fixed;
		inset: 0;
		z-index: 10000;
		background: rgba(0, 0, 0, 0.6);
		display: flex;
		align-items: center;
		justify-content: center;
		opacity: 1;
		transition: opacity 0.15s ease;
	}

	.dialog-panel {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) * 2);
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);
		width: 90vw;
		max-height: 85vh;
		display: flex;
		flex-direction: column;
		outline: none;
		transform: scale(1);
		opacity: 1;
		transition:
			transform 0.15s ease,
			opacity 0.15s ease;
	}

	.dialog-sm {
		max-width: 400px;
	}

	.dialog-md {
		max-width: 600px;
	}

	.dialog-lg {
		max-width: 900px;
	}

	.dialog-fullscreen {
		width: calc(100vw - 32px);
		max-width: calc(100vw - 32px);
		height: calc(100vh - 32px);
		max-height: calc(100vh - 32px);
	}

	.dialog-header {
		padding: 16px 20px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.dialog-content {
		padding: 20px;
		overflow-y: auto;
		flex: 1;
	}

	.dialog-footer {
		padding: 12px 20px;
		border-top: 1px solid var(--border);
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		flex-shrink: 0;
	}
</style>
