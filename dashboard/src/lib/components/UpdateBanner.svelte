<script module lang="ts">
	/**
	 * Module-level reactive flag so other components can detect when a
	 * server-side update is in progress and suppress themselves.
	 * Wrapped in an object because Svelte 5 forbids exporting reassigned $state.
	 */
	export const serverUpdate = $state({ active: false });
</script>

<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { i18n } from '$lib/i18n/index.svelte.js';
	import Button from '$lib/components/Button.svelte';
	import { reportError } from '$lib/errors';

	interface UpdateStatus {
		current: string;
		latest: string | null;
		update_available: boolean;
		last_checked: number;
	}

	// SSE event shape from the update endpoint.
	interface UpdateEvent {
		step: string;
		status: string;
		output?: string;
	}

	type UpdateState = 'idle' | 'updating' | 'done' | 'error';

	let status = $state<UpdateStatus | null>(null);
	let dismissed = $state(false);
	let updateState = $state<UpdateState>('idle');
	let currentStep = $state('');
	let errorOutput = $state('');

	// Map step keys to i18n keys for progress messages.
	const STEP_LABELS: Record<string, string> = {
		pulling: 'update.pulling',
		installing_deps: 'update.installing_deps',
		building: 'update.building',
		restarting: 'update.restarting',
	};

	// Check localStorage for a previously dismissed version.
	function isDismissed(version: string | null): boolean {
		if (!version || typeof localStorage === 'undefined') return false;
		return localStorage.getItem('update-dismissed-version') === version;
	}

	function dismiss() {
		if (status?.latest && typeof localStorage !== 'undefined') {
			localStorage.setItem('update-dismissed-version', status.latest);
		}
		dismissed = true;
	}

	async function fetchStatus() {
		try {
			const data: UpdateStatus = await api.get('/api/system/update-status');
			status = data;
			if (data.latest && isDismissed(data.latest)) {
				dismissed = true;
			}
		} catch (e) {
			reportError(e, { silent: true, category: 'update' });
		}
	}

	async function startUpdate() {
		updateState = 'updating';
		currentStep = 'pulling';
		errorOutput = '';

		try {
			const token =
				typeof sessionStorage !== 'undefined' ? (sessionStorage.getItem('auth-token') ?? '') : '';
			const url = `/api/system/update${token ? `?token=${encodeURIComponent(token)}` : ''}`;
			const resp = await fetch(url, {
				method: 'POST',
				credentials: 'include',
			});

			if (!resp.ok) {
				updateState = 'error';
				errorOutput = `HTTP ${resp.status}`;
				return;
			}

			const reader = resp.body?.getReader();
			if (!reader) {
				updateState = 'error';
				return;
			}

			const decoder = new TextDecoder();
			let buffer = '';

			while (true) {
				const { done, value } = await reader.read();
				if (done) break;
				buffer += decoder.decode(value, { stream: true });

				// Process complete SSE lines from the buffer.
				const lines = buffer.split('\n');
				// Keep the last incomplete line in the buffer.
				buffer = lines.pop() ?? '';

				for (const line of lines) {
					if (!line.startsWith('data: ')) continue;
					try {
						const event: UpdateEvent = JSON.parse(line.slice(6));
						currentStep = event.step;
						if (event.status === 'error') {
							updateState = 'error';
							errorOutput = event.output ?? '';
							return;
						}
						if (event.step === 'restarting' && event.status === 'done') {
							updateState = 'done';
						}
					} catch (e) {
						reportError(e, { silent: true, category: 'update' });
					}
				}
			}

			if (updateState !== 'error') {
				updateState = 'done';
			}
		} catch (e) {
			reportError(e, { category: 'update' });
			updateState = 'error';
			errorOutput = 'Connection lost';
		}
	}

	function reload() {
		window.location.reload();
	}

	onMount(() => {
		fetchStatus();
		// Re-check every 30 minutes.
		const interval = setInterval(fetchStatus, 30 * 60 * 1000);
		return () => clearInterval(interval);
	});

	// Keep module-level flag in sync so other components can react to update state.
	$effect(() => {
		serverUpdate.active = updateState === 'updating' || updateState === 'done';
	});

	const visible = $derived(
		(status !== null && status.update_available && !dismissed && status.latest !== null) ||
			updateState === 'updating' ||
			updateState === 'done' ||
			updateState === 'error',
	);

	const stepLabel = $derived(
		currentStep && STEP_LABELS[currentStep] ? i18n.t(STEP_LABELS[currentStep]) : currentStep,
	);
</script>

{#if visible}
	<div class="update-banner" class:update-error={updateState === 'error'} role="status">
		{#if updateState === 'idle'}
			<span class="update-text">
				{i18n.t('update.available', {
					latest: status?.latest ?? '',
					current: status?.current ?? '',
				})}
			</span>
			<Button variant="ghost" size="sm" class="update-btn" onclick={startUpdate}>
				{i18n.t('update.update')}
			</Button>
			<Button variant="ghost" size="sm" class="update-btn" onclick={dismiss}>
				{i18n.t('update.dismiss')}
			</Button>
		{:else if updateState === 'updating'}
			<span class="update-text">{stepLabel}</span>
		{:else if updateState === 'done'}
			<span class="update-text">{i18n.t('update.complete')}</span>
			<Button variant="ghost" size="sm" class="update-btn" onclick={reload}>
				{i18n.t('update.reload')}
			</Button>
		{:else if updateState === 'error'}
			<span class="update-text"
				>{i18n.t('update.error')}{errorOutput ? `: ${errorOutput}` : ''}</span
			>
			<Button variant="ghost" size="sm" class="update-btn" onclick={dismiss}>
				{i18n.t('update.dismiss')}
			</Button>
		{/if}
	</div>
{/if}

<style>
	.update-banner {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 12px;
		padding: 6px 16px;
		background: var(--accent);
		color: #fff;
		font-size: 12px;
		font-weight: 500;
		flex-shrink: 0;
	}

	.update-error {
		background: var(--danger, #ef4444);
	}

	.update-text {
		flex: 0 1 auto;
	}

	.update-btn {
		font-size: 11px;
		font-weight: 600;
		padding: 2px 10px;
		border-radius: var(--radius);
		border: 1px solid rgba(255, 255, 255, 0.3);
		background: rgba(255, 255, 255, 0.15);
		color: #fff;
		cursor: pointer;
		transition:
			background 0.15s,
			border-color 0.15s;
		white-space: nowrap;
	}

	.update-btn:hover {
		background: rgba(255, 255, 255, 0.25);
		border-color: rgba(255, 255, 255, 0.5);
	}
</style>
