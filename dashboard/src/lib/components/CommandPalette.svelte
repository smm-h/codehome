<script lang="ts">
	import { i18n } from '$lib/i18n/index.svelte.js';
	import Input from '$lib/components/Input.svelte';
	import { allTabs, type Tab } from '$lib/tabs.js';
	import { features } from '$lib/stores/features.svelte.js';
	import { branchUrl } from '$lib/url.js';
	import { getAllActions, type PaletteAction } from '$lib/stores/commandPalette.svelte.js';
	import { api } from '$lib/api.js';
	import { reportError } from '$lib/errors';
	import Icon from '$lib/components/Icon.svelte';
	import Button from '$lib/components/Button.svelte';
	import { goto, page } from '$lib/router/router.svelte.js';
	import { routeTable } from '$lib/router/routes.js';

	interface Props {
		open: boolean;
		onClose: () => void;
	}

	let { open = $bindable(), onClose }: Props = $props();

	let query = $state('');
	let selectedIndex = $state(0);
	let inputEl = $state<HTMLInputElement | null>(null);
	let panelEl = $state<HTMLDivElement | null>(null);
	// Element that had focus before opening, restored on close
	let previouslyFocused: HTMLElement | null = null;

	let branchNames = $state<string[]>([]);
	let branchesFetched = false;

	interface DocEntry { name: string; filename: string }
	let docNames = $state<DocEntry[]>([]);
	let docsFetched = false;

	async function fetchBranches() {
		if (branchesFetched) return;
		try {
			const rows: { qualified: string }[] = await api.get('/api/branches/table');
			branchNames = rows.map((r) => r.qualified);
			branchesFetched = true;
		} catch (e) {
			reportError(e, { silent: true, category: 'command-palette' });
		}
	}

	async function fetchDocs() {
		if (docsFetched) return;
		try {
			docNames = await api.get<DocEntry[]>('/api/docs');
			docsFetched = true;
		} catch (e) {
			reportError(e, { silent: true, category: 'command-palette' });
		}
	}

	interface PageItem { path: string; label: string }
	const pageItems: PageItem[] = routeTable
		.filter((r) => r.layout !== 'none' && !r.path.includes(':'))
		.map((r) => {
			const segments = r.path.split('/').filter(Boolean);
			const label = segments.length === 0
				? 'Home'
				: segments.map((s) => s.charAt(0).toUpperCase() + s.slice(1)).join(' / ');
			return { path: r.path, label };
		});

	// Fuzzy match: all query characters must appear in order in the target
	function fuzzyMatch(target: string, q: string): boolean {
		if (!q) return true;
		const lower = target.toLowerCase();
		const ql = q.toLowerCase();
		let pos = 0;
		for (const ch of ql) {
			pos = lower.indexOf(ch, pos);
			if (pos === -1) return false;
			pos++;
		}
		return true;
	}

	// Extract the current branch qualifier from the URL when on a branch route
	const branchQualified = $derived.by(() => {
		const path = page.url.pathname;
		if (!path.startsWith('/branch/')) return null;
		const segments = path.split('/');
		const repo = segments[2];
		const branch = segments[3];
		return repo && branch ? `${repo}:${branch}` : null;
	});

	const actionResults = $derived(getAllActions().filter((a) => fuzzyMatch(a.label, query)));

	const branchResults = $derived(branchNames.filter((name) => fuzzyMatch(name, query)));

	const tabResults = $derived(
		allTabs().filter((tab) => {
			if (tab.flag && !features.enabled(tab.flag)) return false;
			const label = i18n.t(tab.i18n);
			return fuzzyMatch(label, query) || fuzzyMatch(tab.key, query);
		}),
	);

	const docResults = $derived(
		docNames.filter((d) => fuzzyMatch(d.name, query)),
	);

	const pageResults = $derived(
		pageItems.filter((p) => fuzzyMatch(p.label, query) || fuzzyMatch(p.path, query)),
	);

	type ResultItem =
		| { type: 'action'; action: PaletteAction }
		| { type: 'branch'; qualified: string }
		| { type: 'tab'; tab: Tab }
		| { type: 'doc'; name: string }
		| { type: 'page'; path: string; label: string };

	interface Section {
		labelKey: string;
		startIndex: number;
		count: number;
	}

	const flatResults = $derived.by(() => {
		const items: ResultItem[] = [];
		for (const a of actionResults) items.push({ type: 'action', action: a });
		for (const b of branchResults) items.push({ type: 'branch', qualified: b });
		for (const t of tabResults) items.push({ type: 'tab', tab: t });
		for (const d of docResults) items.push({ type: 'doc', name: d.name });
		for (const p of pageResults) items.push({ type: 'page', path: p.path, label: p.label });
		return items;
	});

	const sections = $derived.by(() => {
		const s: Section[] = [];
		let idx = 0;
		if (actionResults.length > 0) {
			s.push({ labelKey: 'cmd.actions', startIndex: idx, count: actionResults.length });
			idx += actionResults.length;
		}
		if (branchResults.length > 0) {
			s.push({ labelKey: 'cmd.branches', startIndex: idx, count: branchResults.length });
			idx += branchResults.length;
		}
		if (tabResults.length > 0) {
			s.push({ labelKey: 'cmd.tabs', startIndex: idx, count: tabResults.length });
			idx += tabResults.length;
		}
		if (docResults.length > 0) {
			s.push({ labelKey: 'cmd.docs', startIndex: idx, count: docResults.length });
			idx += docResults.length;
		}
		if (pageResults.length > 0) {
			s.push({ labelKey: 'cmd.pages', startIndex: idx, count: pageResults.length });
		}
		return s;
	});

	// Clamp selected index when results change
	$effect(() => {
		if (selectedIndex >= flatResults.length) {
			selectedIndex = Math.max(0, flatResults.length - 1);
		}
	});

	$effect(() => {
		if (open) {
			previouslyFocused = document.activeElement as HTMLElement | null;
			query = '';
			selectedIndex = 0;
			branchesFetched = false;
			docsFetched = false;
			fetchBranches();
			fetchDocs();
			requestAnimationFrame(() => inputEl?.focus());
		} else if (previouslyFocused) {
			previouslyFocused.focus();
			previouslyFocused = null;
		}
	});

	function executeResult(item: ResultItem) {
		switch (item.type) {
			case 'action':
				item.action.execute();
				onClose();
				break;
			case 'branch':
				goto(branchUrl(item.qualified));
				onClose();
				break;
			case 'tab': {
				const tab = item.tab;
				let route: string;
				if (tab.scope === 'branch' && branchQualified) {
					route = branchUrl(branchQualified, tab.key);
				} else if (tab.key === 'overview') {
					route = '/';
				} else {
					route = `/${tab.key}`;
				}
				goto(route);
				onClose();
				break;
			}
			case 'doc':
				goto(`/admin/docs?doc=${encodeURIComponent(item.name)}`);
				onClose();
				break;
			case 'page':
				goto(item.path);
				onClose();
				break;
		}
	}

	function selectResult(index: number) {
		const item = flatResults[index];
		if (!item) return;
		executeResult(item);
	}

	/** Scroll the selected item into view within the results container. */
	function scrollSelectedIntoView() {
		requestAnimationFrame(() => {
			const container = panelEl?.querySelector('.palette-results');
			const el = container?.querySelector('.palette-result.selected') as HTMLElement | null;
			el?.scrollIntoView({ block: 'nearest' });
		});
	}

	function onKeydown(e: KeyboardEvent) {
		switch (e.key) {
			case 'ArrowDown':
				e.preventDefault();
				selectedIndex = Math.min(selectedIndex + 1, flatResults.length - 1);
				scrollSelectedIntoView();
				break;
			case 'ArrowUp':
				e.preventDefault();
				selectedIndex = Math.max(selectedIndex - 1, 0);
				scrollSelectedIntoView();
				break;
			case 'Enter':
				e.preventDefault();
				selectResult(selectedIndex);
				break;
			case 'Escape':
				e.preventDefault();
				onClose();
				break;
			case 'Tab':
				// Trap focus inside the palette: cycle back to input
				if (panelEl) {
					e.preventDefault();
					inputEl?.focus();
				}
				break;
		}
	}

	function onOverlayClick(e: MouseEvent) {
		if (e.target === e.currentTarget) {
			onClose();
		}
	}

	/** Check whether a given flat index is the first item in a section. */
	function sectionHeaderAt(index: number): string | null {
		for (const s of sections) {
			if (s.startIndex === index) return i18n.t(s.labelKey);
		}
		return null;
	}

	function itemLabel(item: ResultItem): string {
		switch (item.type) {
			case 'action':
				return item.action.label;
			case 'branch':
				return item.qualified;
			case 'tab':
				return i18n.t(item.tab.i18n);
			case 'doc':
				return item.name;
			case 'page':
				return item.label;
		}
	}

	function itemIcon(item: ResultItem): string | undefined {
		switch (item.type) {
			case 'action':
				return item.action.icon;
			case 'branch':
				return 'git-branch';
			case 'tab':
				return item.tab.icon;
			case 'doc':
				return 'book-open';
			case 'page':
				return 'layout-dashboard';
		}
	}

	function itemBadge(item: ResultItem): string {
		switch (item.type) {
			case 'action':
				return i18n.t('cmd.actions');
			case 'branch':
				return i18n.t('cmd.branches');
			case 'tab':
				return i18n.t('cmd.tabs');
			case 'doc':
				return i18n.t('cmd.docs');
			case 'page':
				return i18n.t('cmd.pages');
		}
	}
</script>

{#if open}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="palette-overlay" onkeydown={onKeydown} onclick={onOverlayClick}>
		<div
			class="palette-panel"
			role="dialog"
			aria-label={i18n.t('cmd.title')}
			aria-modal="true"
			bind:this={panelEl}
		>
			<Input
				bind:ref={inputEl}
				bind:value={query}
				class="palette-input"
				type="text"
				placeholder={i18n.t('cmd.placeholder')}
				spellcheck="false"
				autocomplete="off"
			/>

			<div class="palette-results">
				{#each flatResults as item, idx (item.type + ':' + itemLabel(item))}
					{@const header = sectionHeaderAt(idx)}
					{#if header}
						<div class="section-header">{header}</div>
					{/if}
					{@const icon = itemIcon(item)}
					<Button
						variant="ghost"
						size="sm"
						class="palette-result {idx === selectedIndex ? 'selected' : ''}"
						onclick={() => selectResult(idx)}
						onmouseenter={() => (selectedIndex = idx)}
					>
						<span class="result-icon">
							{#if icon}
								<Icon name={icon} size={16} />
							{/if}
						</span>
						<span class="result-label">{itemLabel(item)}</span>
						<span class="result-badge">{itemBadge(item)}</span>
					</Button>
				{:else}
					<div class="palette-empty">{i18n.t('cmd.no_results')}</div>
				{/each}
			</div>

			<div class="palette-footer">
				<span class="palette-hint">
					<kbd>&uarr;&darr;</kbd>
					{i18n.t('cmd.navigate')}
					<kbd>&crarr;</kbd>
					{i18n.t('cmd.select')}
					<kbd>esc</kbd>
					{i18n.t('action.close')}
				</span>
			</div>
		</div>
	</div>
{/if}

<style>
	.palette-overlay {
		position: fixed;
		inset: 0;
		z-index: 10000;
		background: rgba(0, 0, 0, 0.6);
		display: flex;
		align-items: flex-start;
		justify-content: center;
		padding-top: min(20vh, 120px);
	}

	.palette-panel {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) * 2);
		width: 90vw;
		max-width: 480px;
		overflow: hidden;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);
	}

	.palette-input {
		width: 100%;
		padding: 14px 16px;
		font-family: var(--font-mono);
		font-size: 15px;
		background: transparent;
		border: none;
		border-bottom: 1px solid var(--border);
		border-radius: 0;
		color: var(--text);
	}

	.palette-input:focus {
		outline: none;
		box-shadow: none;
	}

	.palette-results {
		max-height: 360px;
		overflow-y: auto;
		padding: 4px;
	}

	.section-header {
		padding: 8px 12px 4px;
		font-size: 11px;
		font-weight: 600;
		color: var(--text-dim);
		text-transform: uppercase;
		letter-spacing: 0.04em;
		user-select: none;
	}

	.palette-result {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 8px 12px;
		border-radius: var(--radius);
		color: var(--text);
		text-align: left;
		transition: background 0.1s;
	}

	.palette-result:hover,
	.palette-result.selected {
		background: var(--bg-hover);
	}

	.palette-result.selected {
		color: var(--accent);
	}

	.result-icon {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 20px;
		flex-shrink: 0;
		color: var(--text-dim);
	}

	.palette-result.selected .result-icon {
		color: var(--accent);
	}

	.result-label {
		flex: 1;
		font-size: 14px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.result-badge {
		font-size: 10px;
		font-weight: 500;
		color: var(--text-dim);
		background: var(--bg);
		padding: 2px 6px;
		border-radius: var(--radius);
		flex-shrink: 0;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.palette-result.selected .result-badge {
		color: var(--accent);
		opacity: 0.7;
	}

	.palette-empty {
		padding: 20px 16px;
		text-align: center;
		color: var(--text-dim);
		font-size: 13px;
	}

	.palette-footer {
		border-top: 1px solid var(--border);
		padding: 8px 16px;
	}

	.palette-hint {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 11px;
		color: var(--text-dim);
	}

	kbd {
		display: inline-block;
		padding: 1px 5px;
		font-family: var(--font-mono);
		font-size: 11px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 3px;
		color: var(--text-muted);
	}
</style>
