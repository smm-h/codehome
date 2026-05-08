<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLInputAttributes } from 'svelte/elements';

	interface Props extends HTMLInputAttributes {
		type?: string;
		/** Bindable value -- mirrors the native input's value for two-way binding. */
		value?: string | number;
		/** Bindable checked state -- for checkbox/radio inputs. */
		checked?: boolean;
		/** Optional label -- either a plain string or a snippet for rich content. */
		label?: string | Snippet;
		/** Validation error message displayed below the input. */
		error?: string;
		/** Bindable reference to the underlying <input> element. */
		ref?: HTMLInputElement | null;
	}

	let {
		type = 'text',
		value = $bindable(),
		checked = $bindable(),
		label,
		error,
		class: className,
		ref = $bindable(null),
		oninput: callerOnInput,
		onchange: callerOnChange,
		...rest
	}: Props = $props();

	// Sync the bindable props from input events. The native input receives
	// value/checked as one-way attributes; these handlers push changes back
	// up so bind:value / bind:checked on the component work.
	function handleInput(e: Event) {
		const el = e.currentTarget as HTMLInputElement;
		if (type === 'number' || type === 'range') {
			value = el.valueAsNumber;
		} else {
			value = el.value;
		}
		// Forward to any caller-provided oninput handler.
		if (callerOnInput) (callerOnInput as (e: Event) => void)(e);
	}

	function handleChange(e: Event) {
		const el = e.currentTarget as HTMLInputElement;
		if (type === 'checkbox' || type === 'radio') {
			checked = el.checked;
		}
		// Forward to any caller-provided onchange handler.
		if (callerOnChange) (callerOnChange as (e: Event) => void)(e);
	}
</script>

{#if label || error}
	<label class="input-wrapper {className ?? ''}">
		{#if label}
			{#if typeof label === 'string'}
				<span class="input-label">{label}</span>
			{:else}
				<span class="input-label">{@render label()}</span>
			{/if}
		{/if}
		<input
			bind:this={ref}
			{type}
			value={type !== 'checkbox' && type !== 'radio' ? value ?? '' : undefined}
			checked={type === 'checkbox' || type === 'radio' ? checked : undefined}
			class="input"
			class:input-error={error}
			oninput={handleInput}
			onchange={handleChange}
			{...rest}
		/>
		{#if error}
			<span class="input-error-msg">{error}</span>
		{/if}
	</label>
{:else}
	<input
		bind:this={ref}
		{type}
		value={type !== 'checkbox' && type !== 'radio' ? value ?? '' : undefined}
		checked={type === 'checkbox' || type === 'radio' ? checked : undefined}
		class="input {className ?? ''}"
		class:input-error={error}
		oninput={handleInput}
		onchange={handleChange}
		{...rest}
	/>
{/if}

<style>
	.input-wrapper {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.input-label {
		font-size: 12px;
		font-weight: 500;
		color: var(--text-muted);
	}

	.input {
		font: inherit;
		color: var(--text);
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 6px 10px;
		font-size: 13px;
		transition:
			border-color 0.15s,
			box-shadow 0.15s;
	}

	.input::placeholder {
		color: var(--text-dim);
	}

	.input:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2);
	}

	.input:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.input-error {
		border-color: var(--error, var(--danger));
	}

	.input-error:focus {
		box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.2);
	}

	.input-error-msg {
		font-size: 12px;
		color: var(--error, var(--danger));
	}
</style>
