<script lang="ts">
	import { tick } from 'svelte';
	import Icon from './Icon.svelte';
	import Button from './Button.svelte';

	interface Props {
		lines: string[];
		maxLines?: number;
		/** When true, the viewer auto-scrolls to the bottom on new lines. */
		pinScroll?: boolean;
	}

	let { lines, maxLines = 500, pinScroll = true }: Props = $props();

	let pinned = $derived(pinScroll);
	let scrollEl: HTMLDivElement;

	// Truncate to maxLines from the end so older output is discarded.
	const visibleLines = $derived(
		lines.length > maxLines ? lines.slice(lines.length - maxLines) : lines,
	);

	// Auto-scroll when pinned and lines change.
	$effect(() => {
		// Access visibleLines.length to track changes.
		const _len = visibleLines.length;
		if (pinned && scrollEl) {
			tick().then(() => {
				scrollEl.scrollTop = scrollEl.scrollHeight;
			});
		}
	});

	function togglePin() {
		pinned = !pinned;
		if (pinned && scrollEl) {
			scrollEl.scrollTop = scrollEl.scrollHeight;
		}
	}

	/** Detect manual scroll: unpin when user scrolls away from bottom. */
	function handleScroll() {
		if (!scrollEl) return;
		const atBottom = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 24;
		if (pinned && !atBottom) {
			pinned = false;
		}
	}
</script>

<div class="log-viewer" role="log" aria-live="polite">
	<div class="log-toolbar">
		<span class="log-line-count">{lines.length} lines</span>
		<Button
			variant="ghost"
			size="sm"
			class="log-pin-btn {pinned ? 'active' : ''}"
			onclick={togglePin}
			aria-label={pinned ? 'Unpin scroll' : 'Pin to bottom'}
			title={pinned ? 'Unpin scroll' : 'Pin to bottom'}
		>
			<Icon name="arrow-down" size={14} />
		</Button>
	</div>
	<div class="log-scroll" bind:this={scrollEl} onscroll={handleScroll}>
		<pre class="log-pre">{#each visibleLines as line, i (i)}<span class="log-line"
					>{line}
</span>{/each}</pre>
	</div>
</div>

<style>
	.log-viewer {
		display: flex;
		flex-direction: column;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
		min-height: 120px;
		max-height: 480px;
	}

	.log-toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 4px 10px;
		background: var(--bg-surface);
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.log-line-count {
		font-size: 11px;
		color: var(--text-dim);
		font-family: var(--font-mono);
	}

	.log-pin-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 24px;
		height: 24px;
		border-radius: 4px;
		color: var(--text-dim);
		transition:
			color 0.15s,
			background 0.15s;
	}

	.log-pin-btn:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.log-pin-btn.active {
		color: var(--accent);
	}

	.log-scroll {
		flex: 1;
		overflow-y: auto;
		overflow-x: auto;
		padding: 8px 12px;
	}

	.log-pre {
		margin: 0;
		font-family: var(--font-mono);
		font-size: 12px;
		line-height: 1.6;
		color: var(--text);
		white-space: pre;
		/* Prevent wrapping -- horizontal scroll instead. */
		word-break: keep-all;
	}

	.log-line {
		display: inline;
	}
</style>
