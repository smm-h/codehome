<script lang="ts">
	import type { DataGridColumn, DataGridState } from './types';
	import { SvelteSet } from 'svelte/reactivity';

	interface Props {
		gridId: string;
		columns: DataGridColumn[];
		rows: any[];
		totalRows?: number;
		pageSize?: number;
		sortable?: boolean;
		filterable?: boolean;
		selectable?: boolean;
	}

	let {
		gridId,
		columns,
		rows,
		totalRows,
		pageSize = 25,
		sortable = true,
		filterable = true,
		selectable = false,
	}: Props = $props();

	let sortKey: string | null = $state(null);
	let sortDir: 'asc' | 'desc' = $state('asc');
	let filterQuery: string = $state('');
	let currentPage: number = $state(0);
	let selectedRows = new SvelteSet<number>();
	let editMode: boolean = $state(false);
	let columnOrder: string[] = $state(columns.map((c) => c.key));
	let visibleColumns = new SvelteSet<string>(columns.filter((c) => !c.hidden).map((c) => c.key));
	let currentPageSize: number = $state(pageSize);
	let columnDropdownOpen: boolean = $state(false);

	let dragSource: number | null = $state(null);
	let dragOver: number | null = $state(null);

	const STORAGE_KEY = $derived(`datagrid:${gridId}`);

	$effect(() => {
		const saved = localStorage.getItem(STORAGE_KEY);
		if (saved) {
			try {
				const parsed = JSON.parse(saved) as { columnOrder?: string[]; visibleColumns?: string[] };
				if (parsed.columnOrder) columnOrder = parsed.columnOrder;
				if (parsed.visibleColumns) {
					visibleColumns.clear();
					for (const k of parsed.visibleColumns) visibleColumns.add(k);
				}
			} catch {}
		}
	});

	function persist() {
		localStorage.setItem(
			STORAGE_KEY,
			JSON.stringify({
				columnOrder,
				visibleColumns: [...visibleColumns],
			}),
		);
	}

	let orderedVisibleColumns = $derived.by(() => {
		return columnOrder
			.filter((key) => visibleColumns.has(key))
			.map((key) => columns.find((c) => c.key === key)!)
			.filter(Boolean);
	});

	let filteredRows = $derived.by(() => {
		if (!filterQuery.trim()) return rows;
		const q = filterQuery.toLowerCase();
		const visKeys = orderedVisibleColumns.map((c) => c.key);
		return rows.filter((row) =>
			visKeys.some((key) => {
				const val = row[key];
				return val != null && String(val).toLowerCase().includes(q);
			}),
		);
	});

	let sortedRows = $derived.by(() => {
		if (!sortKey) return filteredRows;
		const key = sortKey;
		const dir = sortDir === 'asc' ? 1 : -1;
		return [...filteredRows].sort((a, b) => {
			const av = a[key];
			const bv = b[key];
			if (av == null && bv == null) return 0;
			if (av == null) return 1;
			if (bv == null) return -1;
			if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
			return String(av).localeCompare(String(bv)) * dir;
		});
	});

	let total = $derived(totalRows ?? sortedRows.length);
	let totalPages = $derived(Math.max(1, Math.ceil(total / currentPageSize)));
	let pagedRows = $derived.by(() => {
		const start = currentPage * currentPageSize;
		return sortedRows.slice(start, start + currentPageSize);
	});
	let rangeStart = $derived(currentPage * currentPageSize + 1);
	let rangeEnd = $derived(Math.min((currentPage + 1) * currentPageSize, total));

	function handleSort(col: DataGridColumn) {
		if (!sortable || col.sortable === false) return;
		if (sortKey === col.key) {
			if (sortDir === 'asc') {
				sortDir = 'desc';
			} else {
				sortKey = null;
				sortDir = 'asc';
			}
		} else {
			sortKey = col.key;
			sortDir = 'asc';
		}
		currentPage = 0;
	}

	function setPageSize(size: number) {
		currentPageSize = size;
		currentPage = 0;
	}

	function toggleSelectRow(idx: number) {
		const globalIdx = currentPage * currentPageSize + idx;
		if (selectedRows.has(globalIdx)) {
			selectedRows.delete(globalIdx);
		} else {
			selectedRows.add(globalIdx);
		}
	}

	function toggleSelectAll() {
		const start = currentPage * currentPageSize;
		const end = start + pagedRows.length;
		const allSelected = pagedRows.every((_, i) => selectedRows.has(start + i));
		if (allSelected) {
			for (let i = start; i < end; i++) selectedRows.delete(i);
		} else {
			for (let i = start; i < end; i++) selectedRows.add(i);
		}
	}

	let allPageSelected = $derived.by(() => {
		if (pagedRows.length === 0) return false;
		const start = currentPage * currentPageSize;
		return pagedRows.every((_, i) => selectedRows.has(start + i));
	});

	function toggleColumn(key: string) {
		if (visibleColumns.has(key)) {
			if (visibleColumns.size > 1) visibleColumns.delete(key);
		} else {
			visibleColumns.add(key);
		}
		persist();
	}

	function handleDragStart(idx: number) {
		dragSource = idx;
	}

	function handleDragOver(e: DragEvent, idx: number) {
		e.preventDefault();
		dragOver = idx;
	}

	function handleDrop(idx: number) {
		if (dragSource !== null && dragSource !== idx) {
			const newOrder = [...columnOrder];
			const [moved] = newOrder.splice(dragSource, 1);
			newOrder.splice(idx, 0, moved);
			columnOrder = newOrder;
			persist();
		}
		dragSource = null;
		dragOver = null;
	}

	function handleDragEnd() {
		dragSource = null;
		dragOver = null;
	}
</script>

<div class="dg-wrapper">
	<div class="dg-toolbar">
		{#if filterable}
			<input
				type="text"
				class="dg-filter"
				placeholder="Filter..."
				bind:value={filterQuery}
				oninput={() => (currentPage = 0)}
			/>
		{/if}
		<div class="dg-toolbar-right">
			<button class="dg-btn" class:dg-btn-active={editMode} onclick={() => (editMode = !editMode)}>
				Columns
			</button>
		</div>
	</div>

	{#if editMode}
		<div class="dg-edit-bar">
			<div class="dg-col-dropdown-anchor">
				<button class="dg-btn" onclick={() => (columnDropdownOpen = !columnDropdownOpen)}>
					Visibility
				</button>
				{#if columnDropdownOpen}
					<div class="dg-col-dropdown">
						{#each columns as col (col.key)}
							<label class="dg-col-option">
								<input
									type="checkbox"
									checked={visibleColumns.has(col.key)}
									onchange={() => toggleColumn(col.key)}
								/>
								{col.label}
							</label>
						{/each}
					</div>
				{/if}
			</div>
			<span class="dg-edit-hint">Drag column headers to reorder</span>
		</div>
	{/if}

	<div class="dg-table-wrapper">
		<table class="dg-table">
			<thead>
				<tr>
					{#if selectable}
						<th class="dg-th-check">
							<input type="checkbox" checked={allPageSelected} onchange={toggleSelectAll} />
						</th>
					{/if}
					{#each orderedVisibleColumns as col, idx (col.key)}
						<th
							class="dg-th"
							class:dg-th-sortable={sortable && col.sortable !== false}
							class:dg-th-drag-over={editMode && dragOver === idx}
							style={col.width ? `width:${col.width}px` : ''}
							onclick={() => handleSort(col)}
							draggable={editMode}
							ondragstart={() => handleDragStart(idx)}
							ondragover={(e) => handleDragOver(e, idx)}
							ondrop={() => handleDrop(idx)}
							ondragend={handleDragEnd}
						>
							<span class="dg-th-label">{col.label}</span>
							{#if sortable && col.sortable !== false}
								<span class="dg-sort-indicator">
									{#if sortKey === col.key}
										{sortDir === 'asc' ? '▲' : '▼'}
									{:else}
										<span class="dg-sort-neutral">▴</span>
									{/if}
								</span>
							{/if}
						</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each pagedRows as row, idx (idx)}
					<tr
						class="dg-row"
						class:dg-row-selected={selectable &&
							selectedRows.has(currentPage * currentPageSize + idx)}
						onclick={() => selectable && toggleSelectRow(idx)}
					>
						{#if selectable}
							<td class="dg-td-check">
								<input
									type="checkbox"
									checked={selectedRows.has(currentPage * currentPageSize + idx)}
									onclick={(e) => e.stopPropagation()}
									onchange={() => toggleSelectRow(idx)}
								/>
							</td>
						{/if}
						{#each orderedVisibleColumns as col (col.key)}
							<td class="dg-td" style={col.align ? `text-align:${col.align}` : ''}
								>{row[col.key] ?? ''}</td
							>
						{/each}
					</tr>
				{/each}
				{#if pagedRows.length === 0}
					<tr>
						<td
							class="dg-empty"
							colspan={orderedVisibleColumns.length + (selectable ? 1 : 0)}
						>
							No data
						</td>
					</tr>
				{/if}
			</tbody>
		</table>
	</div>

	<div class="dg-footer">
		<span class="dg-showing">Showing {rangeStart}-{rangeEnd} of {total}</span>
		<div class="dg-pagination">
			<button class="dg-btn" disabled={currentPage === 0} onclick={() => currentPage--}>
				Prev
			</button>
			<span class="dg-page-info">Page {currentPage + 1} of {totalPages}</span>
			<button
				class="dg-btn"
				disabled={currentPage >= totalPages - 1}
				onclick={() => currentPage++}
			>
				Next
			</button>
		</div>
		<select
			class="dg-page-size"
			value={currentPageSize}
			onchange={(e) => setPageSize(Number(e.currentTarget.value))}
		>
			<option value={10}>10</option>
			<option value={25}>25</option>
			<option value={50}>50</option>
			<option value={100}>100</option>
		</select>
	</div>
</div>

<style>
	.dg-wrapper {
		display: flex;
		flex-direction: column;
		gap: 8px;
		font-size: 13px;
		color: var(--text);
	}

	.dg-toolbar {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.dg-toolbar-right {
		margin-left: auto;
	}

	.dg-filter {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 5px 10px;
		font-size: 13px;
		color: var(--text);
		outline: none;
		width: 200px;
	}

	.dg-filter:focus {
		border-color: var(--accent);
	}

	.dg-filter::placeholder {
		color: var(--text-muted);
	}

	.dg-btn {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 4px 10px;
		font-size: 12px;
		color: var(--text-muted);
		cursor: pointer;
		transition:
			background 0.15s,
			color 0.15s;
	}

	.dg-btn:hover:not(:disabled) {
		background: var(--bg-hover);
		color: var(--text);
	}

	.dg-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.dg-btn-active {
		background: var(--accent);
		color: var(--accent-text, #fff);
		border-color: var(--accent);
	}

	.dg-edit-bar {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 6px 0;
	}

	.dg-edit-hint {
		font-size: 12px;
		color: var(--text-muted);
	}

	.dg-col-dropdown-anchor {
		position: relative;
	}

	.dg-col-dropdown {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		z-index: 100;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 8px;
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 150px;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
	}

	.dg-col-option {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 12px;
		cursor: pointer;
		color: var(--text);
	}

	.dg-table-wrapper {
		overflow-x: auto;
	}

	.dg-table {
		width: 100%;
		border-collapse: collapse;
	}

	.dg-th {
		text-align: left;
		padding: 8px 10px;
		font-weight: 600;
		font-size: 12px;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
		user-select: none;
		white-space: nowrap;
	}

	.dg-th-sortable {
		cursor: pointer;
	}

	.dg-th-sortable:hover {
		color: var(--text);
	}

	.dg-th-drag-over {
		border-left: 2px solid var(--accent);
	}

	.dg-th-check {
		padding: 8px 10px;
		width: 32px;
		border-bottom: 1px solid var(--border);
	}

	.dg-th-label {
		margin-right: 4px;
	}

	.dg-sort-indicator {
		font-size: 10px;
	}

	.dg-sort-neutral {
		opacity: 0.3;
	}

	.dg-td {
		padding: 6px 10px;
		border-bottom: 1px solid var(--border);
		color: var(--text);
	}

	.dg-td-check {
		padding: 6px 10px;
		width: 32px;
		border-bottom: 1px solid var(--border);
	}

	.dg-row:nth-child(odd) {
		background: var(--bg);
	}

	.dg-row:nth-child(even) {
		background: var(--bg-surface);
	}

	.dg-row:hover {
		background: var(--bg-hover);
	}

	.dg-row-selected {
		background: rgba(99, 102, 241, 0.08) !important;
	}

	.dg-empty {
		text-align: center;
		padding: 24px;
		color: var(--text-muted);
	}

	.dg-footer {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 4px 0;
	}

	.dg-showing {
		font-size: 12px;
		color: var(--text-muted);
	}

	.dg-pagination {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-left: auto;
	}

	.dg-page-info {
		font-size: 12px;
		color: var(--text-muted);
	}

	.dg-page-size {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 3px 6px;
		font-size: 12px;
		color: var(--text);
		cursor: pointer;
	}
</style>
