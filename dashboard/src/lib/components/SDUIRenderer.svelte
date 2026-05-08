<!--
  Recursive SDUI (Server-Driven UI) renderer for plugin dashboard pages.

  Renders a JSON component tree into Svelte 5 markup. Plugins ship a
  `ui.json` file declaring their layout as an SDUINode tree; this
  component walks the tree and renders each node using the dashboard's
  existing design system (CSS variables: --text, --bg, --border, etc.).

  Adapted from ProductEngine's SDUIRenderer but simplified to the
  component subset needed by super/ plugins.

  Supported types:
    Layout:   column, row, stack, spacer, divider, grid, card, empty, breadcrumb
    Display:  text, heading, badge, code-block, progress-bar, spinner, status, timeline, diff, markdown
    Input:    button, input, textarea, select, checkbox, switch, radio, slider
    Data:     list, table, key-value, tree, json-view
    Tabs:     tabs
    Feedback: alert, toast, logs
    Overlay:  modal, drawer, popover, confirm
-->
<script lang="ts" module>
	// ---- Types (exported for consumers) ----

	/** A column definition for the table component. */
	export interface SDUIColumnDef {
		key: string;
		label?: string;
		width?: string;
	}

	/** A key-value entry for the key-value component. */
	export interface SDUIKVEntry {
		key?: string;
		label?: string;
		value?: string;
	}

	export interface SDUINode {
		type: string;
		props?: Record<string, unknown>;
		children?: SDUINode[];
		/** Conditional rendering expression -- node is skipped when falsy. */
		if?: string;
	}

	// ---- Expression evaluation ----

	/**
	 * Resolve a dotted path like "state.foo.bar" or "item.name" against
	 * the provided plugin state and optional list-iteration item.
	 */
	function resolvePath(
		path: string,
		pluginState: Record<string, unknown>,
		item?: unknown,
	): unknown {
		const parts = path.trim().split('.');
		let ctx: unknown;
		if (parts[0] === 'state') {
			ctx = pluginState;
			parts.shift();
		} else if (parts[0] === 'item' && item != null) {
			ctx = item;
			parts.shift();
		} else {
			return undefined;
		}
		for (const p of parts) {
			if (ctx == null || typeof ctx !== 'object') return undefined;
			ctx = (ctx as Record<string, unknown>)[p];
		}
		return ctx;
	}

	/**
	 * Evaluate a single expression (without ${} wrapper).
	 * Handles: state.x.y, item.x, len(state.x)
	 */
	function evalSingle(expr: string, pluginState: Record<string, unknown>, item?: unknown): unknown {
		const trimmed = expr.trim();
		// len() function -- returns array length or 0
		const lenMatch = trimmed.match(/^len\((.+)\)$/);
		if (lenMatch) {
			const val = resolvePath(lenMatch[1], pluginState, item);
			return Array.isArray(val) ? val.length : 0;
		}
		return resolvePath(trimmed, pluginState, item);
	}

	/**
	 * Evaluate a prop value. If the entire value is "${expr}", return the
	 * raw result (can be object/array/number). If mixed with text,
	 * interpolate as a string.
	 */
	export function evaluateExpression(
		value: unknown,
		pluginState: Record<string, unknown>,
		item?: unknown,
	): unknown {
		if (typeof value !== 'string') return value;

		// Single full expression -- return raw value (preserves type)
		const fullMatch = value.match(/^\$\{(.+)\}$/);
		if (fullMatch) {
			return evalSingle(fullMatch[1], pluginState, item);
		}

		// String interpolation -- replace each ${...} with stringified result
		return value.replace(/\$\{([^}]+)\}/g, (_: string, expr: string) => {
			const result = evalSingle(expr, pluginState, item);
			return result != null ? String(result) : '';
		});
	}

	// ---- Style helpers ----

	const SIZE_MAP: Record<string, string> = {
		xs: '11px',
		sm: '12px',
		md: '14px',
		lg: '18px',
		xl: '24px',
	};

	function sizeToCSS(size: string): string {
		return SIZE_MAP[size] ?? size;
	}

	const WEIGHT_MAP: Record<string, string> = {
		normal: '400',
		medium: '500',
		semibold: '600',
		bold: '700',
	};

	function weightToCSS(weight: string): string {
		return WEIGHT_MAP[weight] ?? weight;
	}

	function clampLevel(level: number): number {
		return Math.max(1, Math.min(6, Math.round(level)));
	}

	const SPINNER_SIZE: Record<string, number> = { sm: 16, md: 24, lg: 36 };

	function spinnerSizeToPx(size: string): number {
		return SPINNER_SIZE[size] ?? 24;
	}

	/**
	 * Map severity/color tokens to badge CSS classes.
	 * Falls back to 'badge-gray' for unknown values.
	 */
	function badgeColorClass(color: string): string {
		switch (color) {
			case 'blue':
				return 'sdui-badge-blue';
			case 'green':
			case 'success':
				return 'sdui-badge-green';
			case 'red':
			case 'danger':
			case 'error':
				return 'sdui-badge-red';
			case 'yellow':
			case 'warning':
				return 'sdui-badge-yellow';
			default:
				return 'sdui-badge-gray';
		}
	}

	/**
	 * Map severity to alert CSS class.
	 */
	function alertSeverityClass(severity: string): string {
		switch (severity) {
			case 'error':
			case 'danger':
				return 'sdui-alert-error';
			case 'warning':
				return 'sdui-alert-warning';
			case 'success':
				return 'sdui-alert-success';
			default:
				return 'sdui-alert-info';
		}
	}

	// Color tokens that map to dashboard CSS variables
	const COLOR_MAP: Record<string, string> = {
		bg: 'var(--bg)',
		surface: 'var(--bg-surface)',
		hover: 'var(--bg-hover)',
		text: 'var(--text)',
		muted: 'var(--text-muted)',
		dim: 'var(--text-dim)',
		accent: 'var(--accent)',
		success: 'var(--success)',
		warning: 'var(--warning)',
		danger: 'var(--danger)',
		border: 'var(--border)',
	};

	function resolveColor(v: string): string {
		return COLOR_MAP[v] ?? v;
	}

	/**
	 * Convert a grid `columns` prop to a CSS grid-template-columns value.
	 * A number N becomes `repeat(N, 1fr)`; a string is passed through verbatim.
	 */
	function gridCols(columns: unknown, minWidth?: string): string {
		if (typeof columns === 'number' && columns > 0) {
			return `repeat(${columns}, 1fr)`;
		}
		if (typeof columns === 'string' && columns) {
			return columns;
		}
		// Auto-fill with optional min-width (default 150px)
		const mw = minWidth || '150px';
		return `repeat(auto-fill, minmax(${mw}, 1fr))`;
	}

	/**
	 * Map status variant to the CSS dot color.
	 */
	const STATUS_COLOR: Record<string, string> = {
		success: 'var(--success)',
		error: 'var(--danger)',
		warning: 'var(--warning)',
		info: 'var(--accent)',
		neutral: 'var(--text-dim)',
	};

	function statusColor(variant: string): string {
		return STATUS_COLOR[variant] ?? STATUS_COLOR.neutral;
	}

	/** Timeline dot color based on status string. */
	function timelineDotColor(status: string | undefined): string {
		if (!status) return 'var(--text-dim)';
		return STATUS_COLOR[status] ?? 'var(--text-dim)';
	}

	function resolveSpacing(v: unknown): string {
		if (typeof v === 'number') return `${v}px`;
		return String(v);
	}

	/**
	 * Resolve shorthand style props (bg, p, m, rounded, etc.) into an
	 * inline CSS string, using the dashboard's CSS custom properties.
	 */
	function resolveStyles(props: Record<string, unknown> | undefined): string {
		if (!props) return '';
		const parts: string[] = [];

		if (props.bg) parts.push(`background-color: ${resolveColor(String(props.bg))}`);
		if (props.text_color) parts.push(`color: ${resolveColor(String(props.text_color))}`);
		if (props.p) {
			const v = resolveSpacing(props.p);
			parts.push(`padding: ${v}`);
		}
		if (props.px) {
			const v = resolveSpacing(props.px);
			parts.push(`padding-left: ${v}; padding-right: ${v}`);
		}
		if (props.py) {
			const v = resolveSpacing(props.py);
			parts.push(`padding-top: ${v}; padding-bottom: ${v}`);
		}
		if (props.m) {
			const v = resolveSpacing(props.m);
			parts.push(`margin: ${v}`);
		}
		if (props.mx) {
			const v = resolveSpacing(props.mx);
			parts.push(`margin-left: ${v}; margin-right: ${v}`);
		}
		if (props.my) {
			const v = resolveSpacing(props.my);
			parts.push(`margin-top: ${v}; margin-bottom: ${v}`);
		}
		if (props.rounded)
			parts.push(`border-radius: ${props.rounded === true ? 'var(--radius)' : props.rounded}`);
		if (props.border) parts.push(`border: ${props.border}`);
		if (props.w != null)
			parts.push(`width: ${typeof props.w === 'number' ? props.w + 'px' : props.w}`);
		if (props.h != null)
			parts.push(`height: ${typeof props.h === 'number' ? props.h + 'px' : props.h}`);
		if (props.opacity != null) parts.push(`opacity: ${props.opacity}`);
		if (props.overflow) parts.push(`overflow: ${props.overflow}`);

		return parts.join('; ');
	}
</script>

<script lang="ts">
	// Self-import for recursive rendering (Svelte 5 pattern)
	import SDUIRenderer from './SDUIRenderer.svelte';
	import Button from './Button.svelte';
	import Input from './Input.svelte';
	import JsonTreeViewer from './JsonTreeViewer.svelte';
	import MarkdownViewer from './MarkdownViewer.svelte';
	import LogViewer from './LogViewer.svelte';
	import { DataGrid } from './datagrid/index';
	import { toast as toastStore } from '$lib/stores/toast.svelte.js';
	import { SvelteSet, SvelteMap } from 'svelte/reactivity';

	interface Props {
		node: SDUINode;
		/** Plugin state for ${state.x} expression resolution. */
		state: Record<string, unknown>;
		/** Current list iteration item for ${item.x} expressions. */
		item?: unknown;
		/** Callback when a button dispatches a command. */
		onCommand?: (name: string, params: Record<string, unknown>) => void;
	}

	let { node, state, item = undefined, onCommand }: Props = $props();

	// ---- Prop accessor: evaluates expressions against plugin state ----

	function prop(name: string, fallback?: unknown): unknown {
		const raw = node.props?.[name];
		if (raw === undefined) return fallback;
		return evaluateExpression(raw, state, item);
	}

	// ---- Conditional rendering ----

	function shouldRender(): boolean {
		// Check both node.if and node.props.if for flexibility
		const ifExpr = node.if ?? node.props?.if;
		if (ifExpr == null) return true;
		if (typeof ifExpr === 'boolean') return ifExpr;

		const result = evaluateExpression(ifExpr, state, item);
		// Empty arrays are considered falsy
		if (Array.isArray(result) && result.length === 0) return false;
		return !!result;
	}

	// ---- Button command dispatch ----

	function handleCommand() {
		const command = String(prop('command', ''));
		const params = (node.props?.params ?? {}) as Record<string, unknown>;
		// Evaluate each param value
		const evaluated: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(params)) {
			evaluated[k] = evaluateExpression(v, state, item);
		}
		onCommand?.(command, evaluated);
	}

	// ---- Form input change dispatch ----
	// Sends an "input:change" command with the input's name and new value,
	// so plugin command handlers can update state accordingly.

	function handleInputChange(name: string, value: unknown) {
		onCommand?.('input:change', { name, value });
	}

	// ---- Tabs: track which tab is active per instance ----
	// Cannot use the $state() rune here because the prop `state` shadows it,
	// causing Svelte to interpret `$state` as a store subscription.
	// Instead, use a reactive SvelteMap with a single key.
	let tabState = new SvelteMap<string, string>();

	function getActiveTab(): string {
		return tabState.get('active') ?? '';
	}

	function setActiveTab(value: string) {
		tabState.set('active', value);
	}

	// ---- Tree: track expanded nodes by path ----
	// Uses a reactive Set so toggling a path triggers re-render.
	let treeExpanded = new SvelteSet<string>();

	function toggleTree(path: string) {
		if (treeExpanded.has(path)) {
			treeExpanded.delete(path);
		} else {
			treeExpanded.add(path);
		}
	}

	// ---- Overlay open/close state ----
	// SvelteMap with a single key, same pattern as tabState (avoids $state
	// rune collision with the `state` prop).
	let overlayState = new SvelteMap<string, boolean>();

	function isOverlayOpen(): boolean {
		return overlayState.get('open') ?? false;
	}

	function setOverlayOpen(v: boolean) {
		overlayState.set('open', v);
	}

	// ---- Toast: fire on first render ----
	// The toast primitive dispatches a toast notification into the global
	// store on mount.  It renders nothing -- the ToastContainer handles
	// the visual presentation.
	let toastFired = false;
	$effect(() => {
		if (node.type === 'toast' && !toastFired) {
			toastFired = true;
			const variant = String(prop('variant', 'info')) as 'info' | 'success' | 'warning' | 'error';
			const message = String(prop('message', ''));
			const duration = Number(prop('duration_ms', 3000));
			toastStore.add(variant, message, duration);
		}
	});
</script>

{#if shouldRender()}
	{#if node.type === 'column'}
		<div class="sdui-column" style="gap: {prop('gap', 8)}px; {resolveStyles(node.props)}">
			{#each node.children ?? [] as child (child)}
				<SDUIRenderer node={child} {state} {item} {onCommand} />
			{/each}
		</div>
	{:else if node.type === 'row'}
		<div
			class="sdui-row"
			style="gap: {prop('gap', 8)}px; align-items: {prop(
				'align',
				'center',
			)}; justify-content: {prop('justify', 'flex-start')}; {resolveStyles(node.props)}"
		>
			{#each node.children ?? [] as child (child)}
				<SDUIRenderer node={child} {state} {item} {onCommand} />
			{/each}
		</div>
	{:else if node.type === 'stack'}
		<div
			class="sdui-stack"
			style="{prop('width') ? `width:${prop('width')}px;` : ''} {prop('height')
				? `height:${prop('height')}px;`
				: ''} {resolveStyles(node.props)}"
		>
			{#each node.children ?? [] as child (child)}
				<div style="position:absolute; inset:0;">
					<SDUIRenderer node={child} {state} {item} {onCommand} />
				</div>
			{/each}
		</div>
	{:else if node.type === 'spacer'}
		{@const size = prop('size')}
		<div
			class="sdui-spacer"
			style="flex:{size ? 'none' : 1}; {size
				? `height:${size}px; min-height:${size}px;`
				: ''} {resolveStyles(node.props)}"
		></div>
	{:else if node.type === 'divider'}
		<hr class="sdui-divider" style={resolveStyles(node.props)} />
	{:else if node.type === 'text'}
		<span
			class="sdui-text"
			style="font-size: {sizeToCSS(String(prop('size', 'md')))}; font-weight: {weightToCSS(
				String(prop('weight', 'normal')),
			)}; {resolveStyles(node.props)}">{prop('content', '')}</span
		>
	{:else if node.type === 'heading'}
		{@const level = clampLevel(Number(prop('level', 2)))}
		{#if level === 1}
			<h1 class="sdui-heading" style={resolveStyles(node.props)}>{prop('content', '')}</h1>
		{:else if level === 2}
			<h2 class="sdui-heading" style={resolveStyles(node.props)}>{prop('content', '')}</h2>
		{:else if level === 3}
			<h3 class="sdui-heading" style={resolveStyles(node.props)}>{prop('content', '')}</h3>
		{:else if level === 4}
			<h4 class="sdui-heading" style={resolveStyles(node.props)}>{prop('content', '')}</h4>
		{:else if level === 5}
			<h5 class="sdui-heading" style={resolveStyles(node.props)}>{prop('content', '')}</h5>
		{:else}
			<h6 class="sdui-heading" style={resolveStyles(node.props)}>{prop('content', '')}</h6>
		{/if}
	{:else if node.type === 'badge'}
		<span
			class="sdui-badge {badgeColorClass(String(prop('color', 'gray')))}"
			style={resolveStyles(node.props)}>{prop('content', '')}</span
		>
	{:else if node.type === 'code-block'}
		<pre class="sdui-code-block" style={resolveStyles(node.props)}><code>{prop('content', '')}</code
			></pre>
	{:else if node.type === 'progress-bar'}
		{@const value = Math.max(0, Math.min(100, Number(prop('value', 0))))}
		{@const barColor = String(prop('color', 'var(--accent)'))}
		<div
			class="sdui-progress-bar"
			style={resolveStyles(node.props)}
			role="progressbar"
			aria-valuenow={value}
			aria-valuemin={0}
			aria-valuemax={100}
		>
			<div class="sdui-progress-fill" style="width:{value}%; background:{barColor};"></div>
			{#if prop('label')}
				<span class="sdui-progress-label">{prop('label')}</span>
			{/if}
		</div>
	{:else if node.type === 'spinner'}
		{@const sPx = spinnerSizeToPx(String(prop('size', 'md')))}
		<div class="sdui-spinner" style="width:{sPx}px; height:{sPx}px;"></div>
	{:else if node.type === 'button'}
		<Button
			variant={String(prop('variant', 'primary')) as 'primary' | 'danger' | 'ghost' | 'outline'}
			size={String(prop('size', 'md')) === 'sm' ? 'sm' : 'md'}
			class="sdui-button sdui-button-{prop('variant', 'primary')} sdui-button-{prop('size', 'md')}"
			disabled={!!prop('disabled', false)}
			onclick={handleCommand}
			style={resolveStyles(node.props)}
		>
			{prop('label', '')}
		</Button>
	{:else if node.type === 'list'}
		{@const items = (prop('items', []) as unknown[]) ?? []}
		<div
			class="sdui-list"
			class:sdui-list-horizontal={prop('direction') === 'horizontal'}
			style={resolveStyles(node.props)}
		>
			{#each items as listItem, idx (idx)}
				{#each node.children ?? [] as child (child)}
					<SDUIRenderer node={child} {state} item={listItem} {onCommand} />
				{/each}
			{/each}
		</div>
	{:else if node.type === 'table'}
		{@const columns = (prop('columns', []) as SDUIColumnDef[]) ?? []}
		{@const rows = (prop('rows', []) as Record<string, unknown>[]) ?? []}
		<div class="sdui-table-wrapper" style={resolveStyles(node.props)}>
			<table class="sdui-table">
				<thead>
					<tr>
						{#each columns as col (col.key)}
							<th style={col.width ? `width:${col.width}` : ''}>{col.label ?? col.key}</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each rows as row, idx (idx)}
						<tr>
							{#each columns as col (col.key)}
								<td>{row[col.key] ?? ''}</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{:else if node.type === 'data-grid'}
		{@const dgColumns = (prop('columns', []) as Array<{key: string; label: string; sortable?: boolean; filterable?: boolean; width?: number}>) ?? []}
		{@const dgRows = (prop('data', []) as Record<string, unknown>[]) ?? []}
		{@const dgGridId = String(prop('grid_id', `sdui-dg-${Math.random().toString(36).slice(2, 8)}`))}
		<DataGrid
			gridId={dgGridId}
			columns={dgColumns}
			rows={dgRows}
			totalRows={prop('total_rows') != null ? Number(prop('total_rows')) : undefined}
			pageSize={Number(prop('page_size', 25))}
			sortable={prop('sortable', true) !== false}
			filterable={prop('filterable', true) !== false}
		/>
	{:else if node.type === 'key-value'}
		{@const entries = (prop('entries', []) as SDUIKVEntry[]) ?? []}
		<dl class="sdui-kv" style={resolveStyles(node.props)}>
			{#each entries as entry, idx (idx)}
				<div class="sdui-kv-row">
					<dt>{entry.key ?? entry.label ?? ''}</dt>
					<dd>{entry.value ?? ''}</dd>
				</div>
			{/each}
		</dl>
	{:else if node.type === 'input'}
		<label class="sdui-input" style={resolveStyles(node.props)}>
			{#if prop('label')}<span class="sdui-input-label">{prop('label')}</span>{/if}
			<Input
				class="sdui-input-field"
				type={String(prop('input_type', 'text'))}
				placeholder={String(prop('placeholder', ''))}
				value={String(prop('value', ''))}
				disabled={!!prop('disabled', false)}
				onchange={(e) => handleInputChange(String(prop('name', '')), e.currentTarget.value)}
			/>
		</label>
	{:else if node.type === 'textarea'}
		<label class="sdui-input" style={resolveStyles(node.props)}>
			{#if prop('label')}<span class="sdui-input-label">{prop('label')}</span>{/if}
			<textarea
				class="sdui-textarea-field"
				placeholder={String(prop('placeholder', ''))}
				rows={Number(prop('rows', 3))}
				disabled={!!prop('disabled', false)}
				onchange={(e) => handleInputChange(String(prop('name', '')), e.currentTarget.value)}
				>{String(prop('value', ''))}</textarea
			>
		</label>
	{:else if node.type === 'select'}
		{@const selectOptions = (prop('options', []) as Array<{ label: string; value: string }>) ?? []}
		<label class="sdui-input" style={resolveStyles(node.props)}>
			{#if prop('label')}<span class="sdui-input-label">{prop('label')}</span>{/if}
			<select
				class="sdui-select-field"
				disabled={!!prop('disabled', false)}
				onchange={(e) => handleInputChange(String(prop('name', '')), e.currentTarget.value)}
			>
				{#if prop('placeholder')}
					<option value="" disabled selected>{prop('placeholder')}</option>
				{/if}
				{#each selectOptions as opt (opt.value)}
					<option value={opt.value}>{opt.label}</option>
				{/each}
			</select>
		</label>
	{:else if node.type === 'checkbox'}
		<label class="sdui-checkbox" style={resolveStyles(node.props)}>
			<Input
				type="checkbox"
				class="sdui-checkbox-input"
				checked={!!prop('checked', false)}
				disabled={!!prop('disabled', false)}
				onchange={(e) => handleInputChange(String(prop('name', '')), e.currentTarget.checked)}
			/>
			{#if prop('label')}<span class="sdui-checkbox-label">{prop('label')}</span>{/if}
		</label>
	{:else if node.type === 'switch'}
		<label class="sdui-switch" style={resolveStyles(node.props)}>
			<Input
				type="checkbox"
				class="sdui-switch-input"
				checked={!!prop('checked', false)}
				disabled={!!prop('disabled', false)}
				onchange={(e) => handleInputChange(String(prop('name', '')), e.currentTarget.checked)}
			/>
			<span class="sdui-switch-track">
				<span class="sdui-switch-thumb"></span>
			</span>
			{#if prop('label')}<span class="sdui-switch-label">{prop('label')}</span>{/if}
		</label>
	{:else if node.type === 'radio'}
		{@const radioOptions = (prop('options', []) as Array<{ label: string; value: string }>) ?? []}
		{@const radioName = String(prop('name', ''))}
		<fieldset class="sdui-radio-group" style={resolveStyles(node.props)}>
			{#if prop('label')}<legend class="sdui-input-label">{prop('label')}</legend>{/if}
			{#each radioOptions as opt (opt.value)}
				<label class="sdui-radio">
					<Input
						type="radio"
						class="sdui-radio-input"
						name={radioName}
						value={opt.value}
						disabled={!!prop('disabled', false)}
						onchange={() => handleInputChange(radioName, opt.value)}
					/>
					<span class="sdui-radio-label">{opt.label}</span>
				</label>
			{/each}
		</fieldset>
	{:else if node.type === 'slider'}
		{@const sliderVal = Number(prop('value', prop('min', 0)))}
		<label class="sdui-slider" style={resolveStyles(node.props)}>
			{#if prop('label')}<span class="sdui-input-label">{prop('label')}: {sliderVal}</span>{/if}
			<Input
				type="range"
				class="sdui-slider-input"
				min={Number(prop('min', 0))}
				max={Number(prop('max', 100))}
				step={Number(prop('step', 1))}
				value={sliderVal}
				disabled={!!prop('disabled', false)}
				oninput={(e) => handleInputChange(String(prop('name', '')), Number(e.currentTarget.value))}
			/>
		</label>
	{:else if node.type === 'alert'}
		{@const severity = String(prop('severity', 'info'))}
		<div
			class="sdui-alert {alertSeverityClass(severity)}"
			role="alert"
			style={resolveStyles(node.props)}
		>
			{#if prop('title')}
				<strong class="sdui-alert-title">{prop('title')}</strong>
			{/if}
			<span>{prop('message', '')}</span>
		</div>

		<!-- ---- Layout: grid ---- -->
	{:else if node.type === 'grid'}
		{@const cols = gridCols(prop('columns'), prop('min_width') as string | undefined)}
		<div
			class="sdui-grid"
			style="grid-template-columns: {cols}; gap: {prop('gap')
				? prop('gap') + 'px'
				: '0.5rem'}; {resolveStyles(node.props)}"
		>
			{#each node.children ?? [] as child (child)}
				<SDUIRenderer node={child} {state} {item} {onCommand} />
			{/each}
		</div>

		<!-- ---- Layout: card ---- -->
	{:else if node.type === 'card'}
		<div
			class="sdui-card"
			class:sdui-card-elevated={!!prop('elevated')}
			style="{prop('padding') ? `padding: ${resolveSpacing(prop('padding'))};` : ''} {resolveStyles(
				node.props,
			)}"
		>
			{#if prop('title') || prop('subtitle')}
				<div class="sdui-card-header">
					{#if prop('title')}<h3 class="sdui-card-title">{prop('title')}</h3>{/if}
					{#if prop('subtitle')}<p class="sdui-card-subtitle">{prop('subtitle')}</p>{/if}
				</div>
			{/if}
			<div class="sdui-card-body">
				{#each node.children ?? [] as child (child)}
					<SDUIRenderer node={child} {state} {item} {onCommand} />
				{/each}
			</div>
		</div>

		<!-- ---- Layout: empty ---- -->
	{:else if node.type === 'empty'}
		<div class="sdui-empty" style={resolveStyles(node.props)}>
			{#if prop('icon')}
				<span class="sdui-empty-icon">{prop('icon')}</span>
			{/if}
			{#if prop('message')}
				<span class="sdui-empty-message">{prop('message')}</span>
			{/if}
		</div>

		<!-- ---- Layout: breadcrumb ---- -->
	{:else if node.type === 'breadcrumb'}
		{@const bcItems = (prop('items', []) as Array<{ label: string; href?: string }>) ?? []}
		<nav class="sdui-breadcrumb" aria-label="Breadcrumb" style={resolveStyles(node.props)}>
			{#each bcItems as bc, idx (idx)}
				{#if idx > 0}
					<span class="sdui-breadcrumb-sep">/</span>
				{/if}
				{#if bc.href}
					<a class="sdui-breadcrumb-link" href={bc.href}>{bc.label}</a>
				{:else}
					<span class="sdui-breadcrumb-current">{bc.label}</span>
				{/if}
			{/each}
		</nav>

		<!-- ---- Display: status ---- -->
	{:else if node.type === 'status'}
		{@const variant = String(prop('variant', 'neutral'))}
		<span class="sdui-status" style={resolveStyles(node.props)}>
			<span class="sdui-status-dot" style="background: {statusColor(variant)}"></span>
			<span class="sdui-status-label">{prop('label', '')}</span>
		</span>

		<!-- ---- Display: timeline ---- -->
	{:else if node.type === 'timeline'}
		{@const tlItems =
			(prop('items', []) as Array<{
				label: string;
				time?: string;
				status?: string;
				detail?: string;
			}>) ?? []}
		<div class="sdui-timeline" style={resolveStyles(node.props)}>
			{#each tlItems as tl, idx (idx)}
				<div class="sdui-timeline-item">
					<div class="sdui-timeline-rail">
						<span class="sdui-timeline-dot" style="background: {timelineDotColor(tl.status)}"
						></span>
						{#if idx < tlItems.length - 1}
							<span class="sdui-timeline-line"></span>
						{/if}
					</div>
					<div class="sdui-timeline-content">
						<div class="sdui-timeline-header">
							<span class="sdui-timeline-label">{tl.label}</span>
							{#if tl.time}
								<span class="sdui-timeline-time">{tl.time}</span>
							{/if}
						</div>
						{#if tl.detail}
							<span class="sdui-timeline-detail">{tl.detail}</span>
						{/if}
					</div>
				</div>
			{/each}
		</div>

		<!-- ---- Display: diff ---- -->
	{:else if node.type === 'diff'}
		<div class="sdui-diff" style={resolveStyles(node.props)}>
			<div class="sdui-diff-pane sdui-diff-old">
				<div class="sdui-diff-pane-header">Old</div>
				<pre class="sdui-diff-content">{prop('old_text', '')}</pre>
			</div>
			<div class="sdui-diff-pane sdui-diff-new">
				<div class="sdui-diff-pane-header">New</div>
				<pre class="sdui-diff-content">{prop('new_text', '')}</pre>
			</div>
		</div>

		<!-- ---- Display: markdown (delegates to MarkdownViewer) ---- -->
	{:else if node.type === 'markdown'}
		<div class="sdui-markdown" style={resolveStyles(node.props)}>
			<MarkdownViewer content={String(prop('content', ''))} />
		</div>

		<!-- ---- Data: tree ---- -->
	{:else if node.type === 'tree'}
		{@const treeItems = (
			prop('items_key')
				? evaluateExpression('${state.' + prop('items_key') + '}', state, item)
				: prop('items', [])
		) as unknown[]}
		{@const labelKey = String(prop('label_key', 'label'))}
		{@const childrenKey = String(prop('children_key', 'children'))}
		<div class="sdui-tree" style={resolveStyles(node.props)}>
			{#snippet treeNode(tItems: unknown[], path: string)}
				{#each tItems as tItem, idx (idx)}
					{@const tObj = tItem as Record<string, unknown>}
					{@const nodePath = `${path}.${idx}`}
					{@const hasKids =
						Array.isArray(tObj[childrenKey]) && (tObj[childrenKey] as unknown[]).length > 0}
					{@const expanded = treeExpanded.has(nodePath)}
					<div class="sdui-tree-node">
						<div class="sdui-tree-row">
							{#if hasKids}
								<Button variant="ghost" size="sm" class="sdui-tree-toggle" onclick={() => toggleTree(nodePath)}>
									<!-- eslint-disable-next-line svelte/no-at-html-tags -- Static HTML entity literal for triangle arrow glyph -->
									<span class="sdui-tree-arrow" class:expanded>{@html '&#9654;'}</span>
								</Button>
							{:else}
								<span class="sdui-tree-leaf-spacer"></span>
							{/if}
							<span class="sdui-tree-label">{tObj[labelKey] ?? ''}</span>
						</div>
						{#if hasKids && expanded}
							<div class="sdui-tree-children">
								{@render treeNode(tObj[childrenKey] as unknown[], nodePath)}
							</div>
						{/if}
					</div>
				{/each}
			{/snippet}
			{#if Array.isArray(treeItems)}
				{@render treeNode(treeItems, 'root')}
			{/if}
		</div>

		<!-- ---- Data: json-view (delegates to JsonTreeViewer) ---- -->
	{:else if node.type === 'json-view'}
		{@const rawData = prop('data_key')
			? evaluateExpression('${state.' + prop('data_key') + '}', state, item)
			: prop('data', null)}
		{@const jsonStr =
			typeof rawData === 'string' ? rawData : JSON.stringify(rawData ?? null, null, 2)}
		<div class="sdui-json-view" style={resolveStyles(node.props)}>
			<JsonTreeViewer content={jsonStr} />
		</div>

		<!-- ---- Tabs ---- -->
	{:else if node.type === 'tabs'}
		{@const tabItems = (prop('items', []) as Array<{ label: string; value: string }>) ?? []}
		{@const activeProp = prop('active', '') as string}
		{@const activeVal =
			getActiveTab() || activeProp || (tabItems.length > 0 ? tabItems[0].value : '')}
		{@const children = node.children ?? []}
		<div class="sdui-tabs" style={resolveStyles(node.props)}>
			<div class="sdui-tabs-bar" role="tablist">
				{#each tabItems as tab (tab.value)}
					<Button
						variant="ghost"
						class="sdui-tab-button {activeVal === tab.value ? 'active' : ''}"
						role="tab"
						aria-selected={activeVal === tab.value}
						onclick={() => {
							setActiveTab(tab.value);
						}}
					>
						{tab.label}
					</Button>
				{/each}
			</div>
			<div class="sdui-tabs-content">
				{#each tabItems as tab, idx (tab.value)}
					{#if activeVal === tab.value && idx < children.length}
						<SDUIRenderer node={children[idx]} {state} {item} {onCommand} />
					{/if}
				{/each}
			</div>
		</div>
		<!-- ---- Feedback: toast (renders nothing -- dispatches to global toast store on mount) ---- -->
	{:else if node.type === 'toast'}
		<!-- Intentionally empty: the $effect above fires the toast into ToastContainer -->

		<!-- ---- Feedback: logs (delegates to LogViewer) ---- -->
	{:else if node.type === 'logs'}
		{@const logLines = (() => {
			const src = prop('source_event', '');
			// If a source_event key is provided, try to read lines from state
			if (src) {
				const val = evaluateExpression('${state.' + src + '}', state, item);
				if (Array.isArray(val)) return val.map(String);
			}
			// Fallback: read a 'lines' prop directly
			const raw = prop('lines', []);
			return Array.isArray(raw) ? raw.map(String) : [];
		})()}
		<div style={resolveStyles(node.props)}>
			<LogViewer
				lines={logLines}
				maxLines={Number(prop('max_lines', 200))}
				pinScroll={!!prop('auto_scroll', true)}
			/>
		</div>

		<!-- ---- Overlay: modal (inline implementation to avoid $bindable issues with Dialog) ---- -->
	{:else if node.type === 'modal'}
		{#if prop('title')}
			<Button
				variant="outline"
				class="sdui-button sdui-button-outline sdui-button-md"
				onclick={() => setOverlayOpen(true)}
				style={resolveStyles(node.props)}
			>
				{prop('title')}
			</Button>
		{/if}
		{#if isOverlayOpen()}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="sdui-modal-backdrop"
				onclick={() => setOverlayOpen(false)}
				onkeydown={(e) => {
					if (e.key === 'Escape') setOverlayOpen(false);
				}}
			>
				<div
					class="sdui-modal-panel"
					role="dialog"
					aria-modal="true"
					onclick={(e) => e.stopPropagation()}
					onkeydown={() => {}}
				>
					<div class="sdui-modal-header">
						<h3 class="sdui-modal-title">{prop('title', '')}</h3>
						<button
							class="sdui-modal-close"
							onclick={() => setOverlayOpen(false)}
							aria-label="Close"
						>
							<!-- eslint-disable-next-line svelte/no-at-html-tags -- Static HTML entity for close glyph -->
							{@html '&#10005;'}
						</button>
					</div>
					<div class="sdui-modal-body">
						{#each node.children ?? [] as child (child)}
							<SDUIRenderer node={child} {state} {item} {onCommand} />
						{/each}
					</div>
				</div>
			</div>
		{/if}

		<!-- ---- Overlay: drawer (slide-in panel) ---- -->
	{:else if node.type === 'drawer'}
		{@const position = String(prop('position', 'right'))}
		{@const width = String(prop('width', '320px'))}
		{#if prop('title')}
			<Button
				variant="outline"
				class="sdui-button sdui-button-outline sdui-button-md"
				onclick={() => setOverlayOpen(true)}
				style={resolveStyles(node.props)}
			>
				{prop('title')}
			</Button>
		{/if}
		{#if isOverlayOpen()}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="sdui-drawer-backdrop"
				onclick={() => setOverlayOpen(false)}
				onkeydown={(e) => {
					if (e.key === 'Escape') setOverlayOpen(false);
				}}
			>
				<div
					class="sdui-drawer sdui-drawer-{position}"
					style="width: {width};"
					onclick={(e) => e.stopPropagation()}
					onkeydown={() => {}}
					role="dialog"
					aria-modal="true"
				>
					<div class="sdui-drawer-header">
						<h3 class="sdui-drawer-title">{prop('title', '')}</h3>
						<button
							class="sdui-modal-close"
							onclick={() => setOverlayOpen(false)}
							aria-label="Close"
						>
							<!-- eslint-disable-next-line svelte/no-at-html-tags -- Static HTML entity for close glyph -->
							{@html '&#10005;'}
						</button>
					</div>
					<div class="sdui-drawer-body">
						{#each node.children ?? [] as child (child)}
							<SDUIRenderer node={child} {state} {item} {onCommand} />
						{/each}
					</div>
				</div>
			</div>
		{/if}

		<!-- ---- Overlay: popover (floating content anchored to trigger) ---- -->
	{:else if node.type === 'popover'}
		{@const placement = String(prop('placement', 'bottom'))}
		<div class="sdui-popover-anchor" style={resolveStyles(node.props)}>
			<Button
				variant="outline"
				class="sdui-button sdui-button-outline sdui-button-md"
				onclick={() => setOverlayOpen(!isOverlayOpen())}
			>
				{prop('trigger', 'Open')}
			</Button>
			{#if isOverlayOpen()}
				<div class="sdui-popover sdui-popover-{placement}">
					{#each node.children ?? [] as child (child)}
						<SDUIRenderer node={child} {state} {item} {onCommand} />
					{/each}
				</div>
			{/if}
		</div>

		<!-- ---- Overlay: confirm (button + inline confirmation dialog) ---- -->
	{:else if node.type === 'confirm'}
		<Button
			variant="danger"
			class="sdui-button sdui-button-danger sdui-button-md"
			onclick={() => setOverlayOpen(true)}
			style={resolveStyles(node.props)}
		>
			{prop('title', 'Confirm')}
		</Button>
		{#if isOverlayOpen()}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="sdui-modal-backdrop"
				onclick={() => setOverlayOpen(false)}
				onkeydown={(e) => {
					if (e.key === 'Escape') setOverlayOpen(false);
				}}
			>
				<div
					class="sdui-modal-panel sdui-modal-sm"
					role="alertdialog"
					aria-modal="true"
					onclick={(e) => e.stopPropagation()}
					onkeydown={() => {}}
				>
					<div class="sdui-modal-header">
						<h3 class="sdui-modal-title">{prop('title', 'Confirm')}</h3>
					</div>
					<div class="sdui-modal-body">
						<p class="sdui-confirm-message">{prop('message', '')}</p>
					</div>
					<div class="sdui-confirm-footer">
						<Button
							variant="ghost"
							class="sdui-button sdui-button-ghost sdui-button-md"
							onclick={() => setOverlayOpen(false)}
						>
							{prop('cancel_label', 'Cancel')}
						</Button>
						<Button
							variant="danger"
							class="sdui-button sdui-button-danger sdui-button-md"
							onclick={() => {
								setOverlayOpen(false);
								const action = String(prop('action', ''));
								if (action) onCommand?.(action, {});
							}}
						>
							{prop('confirm_label', 'Confirm')}
						</Button>
					</div>
				</div>
			</div>
		{/if}
	{:else}
		<!-- Unknown node type: render a visible fallback so plugin authors
       can see what went wrong during development. -->
		<div class="sdui-unknown">Unknown component: {node.type}</div>
	{/if}
{/if}

<style>
	/* ---- Layout ---- */

	.sdui-column {
		display: flex;
		flex-direction: column;
	}

	.sdui-row {
		display: flex;
		flex-direction: row;
		flex-wrap: wrap;
	}

	.sdui-stack {
		position: relative;
		min-height: 40px;
	}

	.sdui-spacer {
		pointer-events: none;
	}

	.sdui-divider {
		border: none;
		border-top: 1px solid var(--border);
		margin: 4px 0;
	}

	/* ---- Display ---- */

	.sdui-text {
		color: var(--text);
	}

	.sdui-heading {
		color: var(--text);
		margin: 0;
		font-weight: 600;
	}

	.sdui-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 11px;
		font-weight: 600;
	}
	.sdui-badge-gray {
		background: var(--bg-hover);
		color: var(--text-muted);
	}
	.sdui-badge-blue {
		background: rgba(99, 102, 241, 0.15);
		color: var(--accent);
	}
	.sdui-badge-green {
		background: rgba(34, 197, 94, 0.15);
		color: var(--success);
	}
	.sdui-badge-red {
		background: rgba(239, 68, 68, 0.15);
		color: var(--danger);
	}
	.sdui-badge-yellow {
		background: rgba(245, 158, 11, 0.15);
		color: var(--warning);
	}

	.sdui-code-block {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 8px 12px;
		margin: 0;
		overflow-x: auto;
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--text);
		white-space: pre;
	}

	.sdui-progress-bar {
		position: relative;
		width: 100%;
		height: 8px;
		background: var(--bg-hover);
		border-radius: 4px;
		overflow: hidden;
	}
	.sdui-progress-fill {
		height: 100%;
		border-radius: 4px;
		transition: width 0.2s ease;
	}
	.sdui-progress-label {
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		font-size: 10px;
		color: var(--text);
		pointer-events: none;
	}

	.sdui-spinner {
		border: 3px solid var(--border);
		border-top-color: var(--accent);
		border-radius: 50%;
		animation: sdui-spin 0.8s linear infinite;
	}
	@keyframes sdui-spin {
		to {
			transform: rotate(360deg);
		}
	}

	/* ---- Input (button) ---- */

	.sdui-button {
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
			filter 0.15s;
		white-space: nowrap;
	}
	.sdui-button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.sdui-button-sm {
		padding: 4px 10px;
		font-size: 12px;
	}
	.sdui-button-md {
		padding: 6px 12px;
		font-size: 13px;
	}

	.sdui-button-primary {
		background: var(--accent);
		color: var(--accent-text);
	}
	.sdui-button-primary:hover:not(:disabled) {
		filter: brightness(1.1);
	}

	.sdui-button-danger {
		background: var(--danger);
		color: white;
	}
	.sdui-button-danger:hover:not(:disabled) {
		filter: brightness(1.1);
	}

	.sdui-button-ghost {
		background: transparent;
		color: var(--text-muted);
	}
	.sdui-button-ghost:hover:not(:disabled) {
		background: var(--bg-hover);
		color: var(--text);
	}

	.sdui-button-outline {
		background: transparent;
		color: var(--text-muted);
		border: 1px solid var(--border);
	}
	.sdui-button-outline:hover:not(:disabled) {
		background: var(--bg-hover);
		color: var(--text);
	}

	/* ---- Input (form fields) ---- */

	.sdui-input {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.sdui-input-label {
		font-size: 12px;
		font-weight: 500;
		color: var(--text-muted);
	}

	.sdui-input-field,
	.sdui-textarea-field,
	.sdui-select-field {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 6px 10px;
		font-size: 13px;
		color: var(--text);
		outline: none;
		transition: border-color 0.15s;
	}

	.sdui-input-field:focus,
	.sdui-textarea-field:focus,
	.sdui-select-field:focus {
		border-color: var(--accent);
	}

	.sdui-input-field:disabled,
	.sdui-textarea-field:disabled,
	.sdui-select-field:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.sdui-input-field::placeholder,
	.sdui-textarea-field::placeholder {
		color: var(--text-dim);
	}

	.sdui-textarea-field {
		resize: vertical;
		font-family: inherit;
	}

	.sdui-select-field {
		cursor: pointer;
		appearance: none;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M3 5l3 3 3-3'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 8px center;
		padding-right: 28px;
	}

	/* Checkbox */

	.sdui-checkbox {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
		font-size: 13px;
		color: var(--text);
	}

	.sdui-checkbox-input {
		accent-color: var(--accent);
		margin: 0;
		cursor: pointer;
	}

	.sdui-checkbox-input:disabled {
		cursor: not-allowed;
	}

	.sdui-checkbox-label {
		user-select: none;
	}

	/* Switch (toggle) */

	.sdui-switch {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		cursor: pointer;
		font-size: 13px;
		color: var(--text);
	}

	.sdui-switch-input {
		/* Hidden but accessible */
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
	}

	.sdui-switch-track {
		position: relative;
		display: inline-block;
		width: 34px;
		height: 18px;
		background: var(--bg-hover);
		border: 1px solid var(--border);
		border-radius: 9px;
		transition:
			background 0.15s,
			border-color 0.15s;
	}

	.sdui-switch-input:checked + .sdui-switch-track {
		background: var(--accent);
		border-color: var(--accent);
	}

	.sdui-switch-input:disabled + .sdui-switch-track {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.sdui-switch-thumb {
		position: absolute;
		top: 2px;
		left: 2px;
		width: 12px;
		height: 12px;
		background: white;
		border-radius: 50%;
		transition: transform 0.15s;
	}

	.sdui-switch-input:checked + .sdui-switch-track .sdui-switch-thumb {
		transform: translateX(16px);
	}

	.sdui-switch-label {
		user-select: none;
	}

	/* Radio group */

	.sdui-radio-group {
		display: flex;
		flex-direction: column;
		gap: 6px;
		border: none;
		margin: 0;
		padding: 0;
	}

	.sdui-radio {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
		font-size: 13px;
		color: var(--text);
	}

	.sdui-radio-input {
		accent-color: var(--accent);
		margin: 0;
		cursor: pointer;
	}

	.sdui-radio-input:disabled {
		cursor: not-allowed;
	}

	.sdui-radio-label {
		user-select: none;
	}

	/* Slider (range) */

	.sdui-slider {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.sdui-slider-input {
		width: 100%;
		accent-color: var(--accent);
		cursor: pointer;
	}

	.sdui-slider-input:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	/* ---- Data ---- */

	.sdui-list {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.sdui-list-horizontal {
		flex-direction: row;
		flex-wrap: wrap;
	}

	.sdui-table-wrapper {
		overflow-x: auto;
	}
	.sdui-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}
	.sdui-table th {
		text-align: left;
		padding: 6px 10px;
		font-weight: 600;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
		font-size: 12px;
	}
	.sdui-table td {
		padding: 6px 10px;
		border-bottom: 1px solid var(--border);
		color: var(--text);
	}
	.sdui-table tbody tr:hover {
		background: var(--bg-hover);
	}

	.sdui-kv {
		display: flex;
		flex-direction: column;
		gap: 0;
		margin: 0;
	}
	.sdui-kv-row {
		display: flex;
		justify-content: space-between;
		padding: 6px 0;
		border-bottom: 1px solid var(--border);
	}
	.sdui-kv-row:last-child {
		border-bottom: none;
	}
	.sdui-kv dt {
		color: var(--text-muted);
		font-size: 13px;
	}
	.sdui-kv dd {
		color: var(--text);
		font-size: 13px;
		margin: 0;
		text-align: right;
	}

	/* ---- Feedback (alert) ---- */

	.sdui-alert {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: 10px 14px;
		border-radius: var(--radius);
		font-size: 13px;
		border-left: 3px solid;
	}
	.sdui-alert-title {
		font-weight: 600;
	}
	.sdui-alert-info {
		background: rgba(99, 102, 241, 0.1);
		border-left-color: var(--accent);
		color: var(--text);
	}
	.sdui-alert-success {
		background: rgba(34, 197, 94, 0.1);
		border-left-color: var(--success);
		color: var(--text);
	}
	.sdui-alert-warning {
		background: rgba(245, 158, 11, 0.1);
		border-left-color: var(--warning);
		color: var(--text);
	}
	.sdui-alert-error {
		background: rgba(239, 68, 68, 0.1);
		border-left-color: var(--danger);
		color: var(--text);
	}

	/* ---- Layout: grid ---- */

	.sdui-grid {
		display: grid;
	}

	/* ---- Layout: card ---- */

	.sdui-card {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.sdui-card-elevated {
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
	}

	.sdui-card-header {
		padding: 12px 16px 0;
	}

	.sdui-card-title {
		margin: 0;
		font-size: 14px;
		font-weight: 600;
		color: var(--text);
	}

	.sdui-card-subtitle {
		margin: 2px 0 0;
		font-size: 12px;
		color: var(--text-muted);
	}

	.sdui-card-body {
		padding: 12px 16px;
	}

	/* ---- Layout: empty ---- */

	.sdui-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 8px;
		padding: 32px 16px;
		color: var(--text-dim);
	}

	.sdui-empty-icon {
		font-size: 28px;
		opacity: 0.5;
	}

	.sdui-empty-message {
		font-size: 13px;
	}

	/* ---- Layout: breadcrumb ---- */

	.sdui-breadcrumb {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 13px;
		color: var(--text-muted);
	}

	.sdui-breadcrumb-sep {
		color: var(--text-dim);
		font-size: 11px;
	}

	.sdui-breadcrumb-link {
		color: var(--accent);
		text-decoration: none;
	}

	.sdui-breadcrumb-link:hover {
		text-decoration: underline;
	}

	.sdui-breadcrumb-current {
		color: var(--text);
		font-weight: 500;
	}

	/* ---- Display: status ---- */

	.sdui-status {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 13px;
	}

	.sdui-status-dot {
		display: inline-block;
		width: 8px;
		height: 8px;
		border-radius: 50%;
		flex-shrink: 0;
	}

	.sdui-status-label {
		color: var(--text);
	}

	/* ---- Display: timeline ---- */

	.sdui-timeline {
		display: flex;
		flex-direction: column;
	}

	.sdui-timeline-item {
		display: flex;
		gap: 12px;
		min-height: 40px;
	}

	.sdui-timeline-rail {
		display: flex;
		flex-direction: column;
		align-items: center;
		width: 16px;
		flex-shrink: 0;
	}

	.sdui-timeline-dot {
		width: 10px;
		height: 10px;
		border-radius: 50%;
		flex-shrink: 0;
		margin-top: 4px;
	}

	.sdui-timeline-line {
		flex: 1;
		width: 2px;
		background: var(--border);
		margin-top: 4px;
	}

	.sdui-timeline-content {
		flex: 1;
		padding-bottom: 16px;
	}

	.sdui-timeline-header {
		display: flex;
		align-items: baseline;
		gap: 8px;
	}

	.sdui-timeline-label {
		font-size: 13px;
		font-weight: 500;
		color: var(--text);
	}

	.sdui-timeline-time {
		font-size: 11px;
		color: var(--text-dim);
	}

	.sdui-timeline-detail {
		display: block;
		font-size: 12px;
		color: var(--text-muted);
		margin-top: 2px;
	}

	/* ---- Display: diff ---- */

	.sdui-diff {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1px;
		background: var(--border);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.sdui-diff-pane {
		background: var(--bg);
		display: flex;
		flex-direction: column;
	}

	.sdui-diff-pane-header {
		padding: 4px 10px;
		font-size: 11px;
		font-weight: 600;
		color: var(--text-muted);
		background: var(--bg-surface);
		border-bottom: 1px solid var(--border);
	}

	.sdui-diff-old .sdui-diff-pane-header {
		color: var(--danger);
	}

	.sdui-diff-new .sdui-diff-pane-header {
		color: var(--success);
	}

	.sdui-diff-content {
		margin: 0;
		padding: 8px 10px;
		font-family: var(--font-mono);
		font-size: 12px;
		color: var(--text);
		white-space: pre-wrap;
		word-break: break-all;
		overflow-x: auto;
		flex: 1;
	}

	.sdui-diff-old .sdui-diff-content {
		background: rgba(239, 68, 68, 0.05);
	}

	.sdui-diff-new .sdui-diff-content {
		background: rgba(34, 197, 94, 0.05);
	}

	/* ---- Display: markdown ---- */

	.sdui-markdown {
		overflow: auto;
	}

	/* ---- Data: tree ---- */

	.sdui-tree {
		font-size: 13px;
		color: var(--text);
	}

	.sdui-tree-node {
		display: flex;
		flex-direction: column;
	}

	.sdui-tree-row {
		display: flex;
		align-items: center;
		gap: 4px;
		padding: 2px 0;
	}

	.sdui-tree-toggle {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 16px;
		height: 16px;
		cursor: pointer;
		border-radius: 2px;
		flex-shrink: 0;
		padding: 0;
	}

	.sdui-tree-toggle:hover {
		background: var(--bg-hover);
	}

	.sdui-tree-arrow {
		display: inline-block;
		font-size: 8px;
		color: var(--text-dim);
		transition: transform 0.15s ease;
	}

	.sdui-tree-arrow.expanded {
		transform: rotate(90deg);
	}

	.sdui-tree-leaf-spacer {
		display: inline-block;
		width: 16px;
		flex-shrink: 0;
	}

	.sdui-tree-label {
		color: var(--text);
	}

	.sdui-tree-children {
		padding-left: 20px;
		border-left: 1px solid var(--border);
		margin-left: 7px;
	}

	/* ---- Data: json-view ---- */

	.sdui-json-view {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
	}

	/* ---- Tabs ---- */

	.sdui-tabs {
		display: flex;
		flex-direction: column;
	}

	.sdui-tabs-bar {
		display: flex;
		gap: 0;
		border-bottom: 1px solid var(--border);
		overflow-x: auto;
	}

	/* Hide scrollbar on tab bar */
	.sdui-tabs-bar::-webkit-scrollbar {
		display: none;
	}
	.sdui-tabs-bar {
		scrollbar-width: none;
	}

	.sdui-tab-button {
		padding: 8px 16px;
		font-size: 13px;
		font-weight: 500;
		color: var(--text-muted);
		border-bottom: 2px solid transparent;
		cursor: pointer;
		white-space: nowrap;
		transition:
			color 0.15s,
			border-color 0.15s;
	}

	.sdui-tab-button:hover {
		color: var(--text);
	}

	.sdui-tab-button.active {
		color: var(--accent);
		border-bottom-color: var(--accent);
	}

	.sdui-tabs-content {
		padding: 12px 0;
	}

	/* ---- Overlay: modal / confirm shared ---- */

	.sdui-modal-backdrop {
		position: fixed;
		inset: 0;
		z-index: 10000;
		background: rgba(0, 0, 0, 0.6);
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.sdui-modal-panel {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) * 2);
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);
		width: 90vw;
		max-width: 600px;
		max-height: 85vh;
		display: flex;
		flex-direction: column;
		animation: sdui-modal-in 0.15s ease-out;
	}

	.sdui-modal-sm {
		max-width: 400px;
	}

	@keyframes sdui-modal-in {
		from {
			opacity: 0;
			transform: scale(0.95);
		}
		to {
			opacity: 1;
			transform: scale(1);
		}
	}

	.sdui-modal-body {
		padding: 16px 20px;
		overflow-y: auto;
		flex: 1;
	}

	.sdui-modal-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 14px 20px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.sdui-modal-title {
		margin: 0;
		font-size: 15px;
		font-weight: 600;
		color: var(--text);
	}

	.sdui-modal-close {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		border-radius: var(--radius);
		color: var(--text-dim);
		font-size: 14px;
		cursor: pointer;
		flex-shrink: 0;
		transition:
			background 0.15s,
			color 0.15s;
	}

	.sdui-modal-close:hover {
		background: var(--bg-hover);
		color: var(--text);
	}

	.sdui-confirm-message {
		margin: 0;
		font-size: 13px;
		color: var(--text-muted);
		line-height: 1.5;
	}

	.sdui-confirm-footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding: 12px 20px;
		border-top: 1px solid var(--border);
		flex-shrink: 0;
	}

	/* ---- Overlay: drawer ---- */

	.sdui-drawer-backdrop {
		position: fixed;
		inset: 0;
		z-index: 10000;
		background: rgba(0, 0, 0, 0.5);
	}

	.sdui-drawer {
		position: fixed;
		top: 0;
		bottom: 0;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		z-index: 10001;
		box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
		animation: sdui-drawer-slide-in 0.2s ease-out;
	}

	.sdui-drawer-right {
		right: 0;
		border-left: 1px solid var(--border);
		border-radius: var(--radius) 0 0 var(--radius);
	}

	.sdui-drawer-left {
		left: 0;
		border-right: 1px solid var(--border);
		border-radius: 0 var(--radius) var(--radius) 0;
	}

	@keyframes sdui-drawer-slide-in {
		from {
			opacity: 0;
			transform: translateX(20px);
		}
		to {
			opacity: 1;
			transform: translateX(0);
		}
	}

	.sdui-drawer-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.sdui-drawer-title {
		margin: 0;
		font-size: 15px;
		font-weight: 600;
		color: var(--text);
	}

	.sdui-drawer-body {
		flex: 1;
		overflow-y: auto;
		padding: 16px;
	}

	/* ---- Overlay: popover ---- */

	.sdui-popover-anchor {
		position: relative;
		display: inline-block;
	}

	.sdui-popover {
		position: absolute;
		z-index: 9000;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
		padding: 12px;
		min-width: 160px;
		animation: sdui-popover-in 0.12s ease-out;
	}

	@keyframes sdui-popover-in {
		from {
			opacity: 0;
			transform: scale(0.96);
		}
		to {
			opacity: 1;
			transform: scale(1);
		}
	}

	.sdui-popover-bottom {
		top: calc(100% + 6px);
		left: 0;
	}

	.sdui-popover-top {
		bottom: calc(100% + 6px);
		left: 0;
	}

	.sdui-popover-left {
		right: calc(100% + 6px);
		top: 0;
	}

	.sdui-popover-right {
		left: calc(100% + 6px);
		top: 0;
	}

	/* ---- Unknown type fallback ---- */

	.sdui-unknown {
		background: var(--bg-surface);
		color: var(--text-dim);
		padding: 4px 8px;
		border-radius: var(--radius);
		font-size: 12px;
		border: 1px dashed var(--border);
	}
</style>
