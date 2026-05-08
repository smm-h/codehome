<script lang="ts">
	import { SvelteSet } from 'svelte/reactivity';
	import { reportError } from '$lib/errors';
	import Button from './Button.svelte';

	interface Props {
		content: string;
	}

	let { content }: Props = $props();

	// Parse result: either valid JSON or an error message.
	let parsed = $derived.by(() => {
		try {
			return { ok: true as const, value: JSON.parse(content) };
		} catch (e) {
			return { ok: false as const, error: e instanceof Error ? e.message : 'Invalid JSON' };
		}
	});

	// Track which paths are expanded. Root is expanded by default.
	let expandedPaths = new SvelteSet<string>(['$']);

	function toggle(path: string) {
		if (expandedPaths.has(path)) {
			expandedPaths.delete(path);
		} else {
			expandedPaths.add(path);
		}
	}

	function isExpanded(path: string): boolean {
		return expandedPaths.has(path);
	}

	/** Copy a value to clipboard and briefly show feedback via title change. */
	async function copyValue(value: unknown, event: MouseEvent) {
		const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
		try {
			await navigator.clipboard.writeText(text);
			const target = event.currentTarget as HTMLElement;
			const original = target.title;
			target.title = 'Copied!';
			setTimeout(() => {
				target.title = original;
			}, 1200);
		} catch (e) {
			reportError(e, { category: 'clipboard' });
		}
	}

	/** Return the type category for a value (for CSS coloring). */
	function valueType(val: unknown): string {
		if (val === null) return 'null';
		if (typeof val === 'boolean') return 'boolean';
		if (typeof val === 'number') return 'number';
		if (typeof val === 'string') return 'string';
		if (Array.isArray(val)) return 'array';
		return 'object';
	}

	/** Format a primitive value for display. */
	function formatValue(val: unknown): string {
		if (val === null) return 'null';
		if (typeof val === 'string') return `"${val}"`;
		return String(val);
	}

	/** Summary text for a collapsed container. */
	function collapsedSummary(val: unknown): string {
		if (Array.isArray(val)) {
			return `[${val.length} item${val.length !== 1 ? 's' : ''}]`;
		}
		if (val && typeof val === 'object') {
			const count = Object.keys(val).length;
			return `{${count} key${count !== 1 ? 's' : ''}}`;
		}
		return '';
	}

	/** Check whether a value is a container (object or array). */
	function isContainer(val: unknown): boolean {
		return val !== null && typeof val === 'object';
	}
</script>

{#if !parsed.ok}
	<div class="json-error">
		<div class="json-error-title">Invalid JSON</div>
		<div class="json-error-message">{parsed.error}</div>
		<pre class="json-error-raw"><code>{content}</code></pre>
	</div>
{:else}
	<div class="json-tree">
		{@render jsonNode(parsed.value, '$', '', false)}
	</div>
{/if}

{#snippet jsonNode(value: unknown, path: string, key: string, isArrayItem: boolean)}
	{#if isContainer(value)}
		{@const expanded = isExpanded(path)}
		{@const isArr = Array.isArray(value)}
		{@const entries = isArr
			? (value as unknown[]).map((v, i) => [String(i), v] as const)
			: Object.entries(value as Record<string, unknown>)}
		<div class="json-node">
			<Button variant="ghost" size="sm" class="json-toggle" onclick={() => toggle(path)} title="Click to copy">
				<!-- eslint-disable-next-line svelte/no-at-html-tags -- Static HTML entity literal for triangle arrow glyph -->
				<span class="json-arrow" class:expanded>{@html '&#9654;'}</span>
				{#if key !== ''}
					{#if isArrayItem}
						<span class="json-index">[{key}]</span>
					{:else}
						<span class="json-key">"{key}"</span>
					{/if}
					<span class="json-colon">:</span>
				{/if}
				{#if expanded}
					<span class="json-bracket">{isArr ? '[' : '{'}</span>
				{:else}
					<span class="json-summary">{collapsedSummary(value)}</span>
				{/if}
			</Button>
			{#if expanded}
				<div class="json-children">
					{#each entries as [k, v], idx (k)}
						{@render jsonNode(v, `${path}.${k}`, k, isArr)}
						{#if idx < entries.length - 1}
							<!-- comma after each entry except last -->
						{/if}
					{/each}
				</div>
				<span class="json-bracket json-bracket-close">{isArr ? ']' : '}'}</span>
			{/if}
		</div>
	{:else}
		<!-- Leaf value -->
		<div class="json-leaf">
			{#if key !== ''}
				{#if isArrayItem}
					<span class="json-index">[{key}]</span>
				{:else}
					<span class="json-key">"{key}"</span>
				{/if}
				<span class="json-colon">:</span>
			{/if}
			<Button
				variant="ghost"
				size="sm"
				class="json-value json-value-{valueType(value)}"
				onclick={(e) => copyValue(value, e)}
				title="Click to copy"
			>
				{formatValue(value)}
			</Button>
		</div>
	{/if}
{/snippet}

<style>
	.json-tree {
		flex: 1;
		overflow: auto;
		padding: 12px 16px;
		font-family: var(--font-mono, monospace);
		font-size: 12px;
		line-height: 1.7;
		background: var(--bg);
		color: var(--text);
	}

	/* Error fallback */
	.json-error {
		flex: 1;
		overflow: auto;
		padding: 16px;
	}

	.json-error-title {
		color: var(--danger);
		font-weight: 600;
		margin-bottom: 4px;
	}

	.json-error-message {
		color: var(--text-muted);
		font-size: 12px;
		margin-bottom: 12px;
	}

	.json-error-raw {
		margin: 0;
		padding: 12px;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: auto;
		font-family: var(--font-mono, monospace);
		font-size: 12px;
		color: var(--text);
		white-space: pre-wrap;
		word-break: break-all;
	}

	.json-error-raw code {
		display: block;
	}

	/* Tree structure */
	.json-node {
		display: flex;
		flex-direction: column;
	}

	.json-children {
		padding-left: 20px;
		border-left: 1px solid var(--border);
		margin-left: 6px;
	}

	/* Toggle button for expandable nodes */
	.json-toggle {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		text-align: left;
		cursor: pointer;
		white-space: nowrap;
		padding: 0;
		border-radius: 2px;
	}

	.json-toggle:hover {
		background: var(--bg-hover);
	}

	/* Arrow indicator */
	.json-arrow {
		display: inline-block;
		width: 12px;
		font-size: 8px;
		color: var(--text-dim);
		transition: transform 0.15s ease;
		flex-shrink: 0;
	}

	.json-arrow.expanded {
		transform: rotate(90deg);
	}

	/* Key styling */
	.json-key {
		color: var(--accent);
	}

	.json-index {
		color: var(--text-dim);
		font-size: 11px;
	}

	.json-colon {
		color: var(--text-dim);
		margin-right: 4px;
	}

	/* Bracket and summary */
	.json-bracket {
		color: var(--text-dim);
	}

	.json-bracket-close {
		padding-left: 0;
	}

	.json-summary {
		color: var(--text-dim);
		font-style: italic;
		font-size: 11px;
	}

	/* Leaf values */
	.json-leaf {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		padding-left: 16px;
		white-space: nowrap;
	}

	.json-value {
		cursor: pointer;
		border-radius: 2px;
		padding: 0 2px;
		transition: background 0.1s;
	}

	.json-value:hover {
		background: var(--bg-hover);
	}

	.json-value-string {
		color: var(--success);
	}

	.json-value-number {
		color: var(--warning);
	}

	.json-value-boolean {
		color: var(--accent);
	}

	.json-value-null {
		color: var(--text-dim);
		font-style: italic;
	}
</style>
