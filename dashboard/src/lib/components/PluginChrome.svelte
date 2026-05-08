<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';

	interface Props {
		pluginName: string;
		pluginIcon: string;
		children: Snippet;
	}

	let { pluginName, pluginIcon, children }: Props = $props();

	function goBack() {
		if (window.history.length > 1) {
			window.history.back();
		} else {
			window.location.href = '/';
		}
	}
</script>

<div class="plugin-chrome">
	<div class="chrome-bar">
		<button class="chrome-back" onclick={goBack} aria-label="Go back">
			<Icon name="arrow-left" size={16} />
		</button>
		<Icon name={pluginIcon} size={18} />
		<span class="chrome-title">{pluginName}</span>
	</div>
	<div class="chrome-content">
		{@render children()}
	</div>
</div>

<style>
	.plugin-chrome {
		display: flex;
		flex-direction: column;
		height: 100%;
	}

	.chrome-bar {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 12px;
		background: var(--bg-surface);
		border-bottom: 1px solid var(--border);
		color: var(--text-muted);
		flex-shrink: 0;
	}

	.chrome-back {
		all: unset;
		display: flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		border-radius: var(--radius);
		color: var(--text-dim);
		cursor: pointer;
		transition:
			color 0.15s,
			background 0.15s;
	}

	.chrome-back:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.chrome-title {
		font-size: 14px;
		font-weight: 500;
		color: var(--text);
	}

	.chrome-content {
		flex: 1;
		overflow-y: auto;
	}
</style>
