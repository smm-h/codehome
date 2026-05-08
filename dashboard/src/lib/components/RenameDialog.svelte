<script lang="ts">
	import { i18n } from '$lib/i18n/index.svelte.js';
	import { api } from '$lib/api.js';
	import Input from '$lib/components/Input.svelte';
	import { toast } from '$lib/stores/toast.svelte.js';
	import Dialog from './Dialog.svelte';
	import Button from './Button.svelte';

	interface Props {
		/** The qualified branch name (e.g. "bag:my-branch") */
		qualified: string;
		/** Called after a successful rename with the new qualified name */
		onRenamed: (newQualified: string) => void;
	}

	let { qualified, onRenamed }: Props = $props();

	let open = $state(false);
	let newName = $state('');
	let loading = $state(false);

	// Extract the repo prefix from the qualified name for the preview.
	const repo = $derived(qualified.split(':', 2)[0]);
	const currentBranch = $derived(qualified.split(':', 2)[1] ?? '');
	const previewQualified = $derived(newName.trim() ? `${repo}:${newName.trim()}` : '');

	/** Open the dialog, pre-filling with the current branch name. */
	export function show() {
		newName = currentBranch;
		loading = false;
		open = true;
	}

	function handleClose() {
		open = false;
	}

	async function handleRename() {
		const trimmed = newName.trim();
		if (!trimmed || trimmed === currentBranch) return;

		loading = true;
		try {
			const result = await api.post<{ new_qualified: string }>(`/api/branches/${qualified}/rename`, {
				new_name: trimmed,
			});
			toast.success(i18n.t('branch.rename_success', { name: result.new_qualified }));
			open = false;
			onRenamed(result.new_qualified);
		} catch (err: unknown) {
			toast.error(err instanceof Error ? err.message : i18n.t('branch.rename_error'));
		} finally {
			loading = false;
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !loading && newName.trim() && newName.trim() !== currentBranch) {
			e.preventDefault();
			handleRename();
		}
	}
</script>

<Dialog bind:open size="sm" label={i18n.t('branch.rename_title')}>
	{#snippet header()}
		<h3 class="dialog-title">{i18n.t('branch.rename_title')}</h3>
	{/snippet}

	<div class="rename-form">
		<label class="rename-label" for="rename-input">
			{i18n.t('branch.rename_input_label')}
		</label>
		<Input
			id="rename-input"
			type="text"
			class="rename-input"
			bind:value={newName}
			onkeydown={handleKeydown}
			placeholder={currentBranch}
			disabled={loading}
		/>
		<span class="rename-hint">{i18n.t('branch.rename_input_hint')}</span>
		{#if previewQualified && newName.trim() !== currentBranch}
			<span class="rename-preview">
				{i18n.t('branch.rename_preview', { qualified: previewQualified })}
			</span>
		{/if}
	</div>

	{#snippet footer()}
		<Button variant="ghost" onclick={handleClose} disabled={loading}>
			{i18n.t('action.cancel')}
		</Button>
		<Button
			variant="primary"
			onclick={handleRename}
			disabled={!newName.trim() || newName.trim() === currentBranch}
			{loading}
		>
			{loading ? i18n.t('branch.renaming') : i18n.t('branch.rename')}
		</Button>
	{/snippet}
</Dialog>

<style>
	.dialog-title {
		margin: 0;
		font-size: 15px;
		font-weight: 600;
		color: var(--text);
	}

	.rename-form {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.rename-label {
		font-size: 12px;
		font-weight: 500;
		color: var(--text-muted);
	}

	.rename-input {
		width: 100%;
		padding: 8px 10px;
		font-size: 13px;
		font-family: var(--font-mono);
		color: var(--text);
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		outline: none;
		transition: border-color 0.15s;
	}

	.rename-input:focus {
		border-color: var(--accent);
	}

	.rename-input:disabled {
		opacity: 0.5;
	}

	.rename-hint {
		font-size: 11px;
		color: var(--text-dim);
	}

	.rename-preview {
		font-size: 12px;
		color: var(--text-muted);
		font-family: var(--font-mono);
		padding: 4px 0;
	}
</style>
