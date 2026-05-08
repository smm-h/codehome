<script lang="ts">
	interface Props {
		variant: 'lines' | 'card' | 'table' | 'grid';
	}

	let { variant }: Props = $props();
</script>

{#if variant === 'lines'}
	<div class="skeleton skeleton-lines">
		<div class="skel-line" style="width: 60%"></div>
		<div class="skel-line" style="width: 80%"></div>
		<div class="skel-line" style="width: 70%"></div>
		<div class="skel-line" style="width: 50%"></div>
	</div>
{:else if variant === 'card'}
	<div class="skeleton skeleton-card-block"></div>
{:else if variant === 'table'}
	<div class="skeleton skeleton-table">
		<div class="skel-table-header">
			<div class="skel-cell" style="width: 30%"></div>
			<div class="skel-cell" style="width: 25%"></div>
			<div class="skel-cell" style="width: 20%"></div>
			<div class="skel-cell" style="width: 15%"></div>
		</div>
		{#each { length: 5 } as _}
			<div class="skel-table-row">
				<div class="skel-cell" style="width: 30%"></div>
				<div class="skel-cell" style="width: 25%"></div>
				<div class="skel-cell" style="width: 20%"></div>
				<div class="skel-cell" style="width: 15%"></div>
			</div>
		{/each}
	</div>
{:else if variant === 'grid'}
	<div class="skeleton skeleton-grid">
		{#each { length: 6 } as _}
			<div class="skel-grid-card"></div>
		{/each}
	</div>
{/if}

<style>
	.skeleton {
		width: 100%;
	}

	@keyframes shimmer {
		0%,
		100% {
			opacity: 0.3;
		}
		50% {
			opacity: 0.7;
		}
	}

	.skeleton-lines {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.skel-line {
		height: 14px;
		background: var(--bg-hover);
		border-radius: var(--radius);
		animation: shimmer 1.5s ease-in-out infinite;
	}

	.skeleton-card-block {
		height: 200px;
		background: var(--bg-hover);
		border-radius: var(--radius);
		animation: shimmer 1.5s ease-in-out infinite;
	}

	.skeleton-table {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.skel-table-header {
		display: flex;
		gap: 12px;
		padding-bottom: 8px;
		border-bottom: 1px solid var(--bg-hover);
	}

	.skel-table-row {
		display: flex;
		gap: 12px;
		padding: 4px 0;
	}

	.skel-cell {
		height: 12px;
		background: var(--bg-hover);
		border-radius: var(--radius);
		animation: shimmer 1.5s ease-in-out infinite;
	}

	.skel-table-header .skel-cell {
		height: 14px;
		opacity: 0.8;
	}

	.skeleton-grid {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 12px;
	}

	.skel-grid-card {
		height: 140px;
		background: var(--bg-hover);
		border-radius: var(--radius);
		animation: shimmer 1.5s ease-in-out infinite;
	}
</style>
