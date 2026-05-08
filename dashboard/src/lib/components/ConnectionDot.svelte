<script lang="ts">
	import { sse } from '$lib/stores/sse.svelte.js';
</script>

<span
	class="connection-dot"
	class:connected={sse.connected}
	title={sse.connected ? 'Connected' : 'Disconnected'}
></span>

<style>
	.connection-dot {
		display: inline-block;
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--danger);
		flex-shrink: 0;
	}

	.connection-dot.connected {
		background: var(--success);
	}
</style>
