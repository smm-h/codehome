<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte.js';
	import { i18n } from '$lib/i18n/index.svelte.js';
	import Input from '$lib/components/Input.svelte';
	import Button from '$lib/components/Button.svelte';
	import { goto } from '$lib/router/router.svelte.js';
	import { reportError } from '$lib/errors';

	let username = $state('');
	let password = $state('');
	let error = $state('');
	let submitting = $state(false);

	async function handleSubmit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		submitting = true;

		try {
			const ok = await auth.login(username, password);
			if (ok) {
				await goto('/');
			} else {
				error = i18n.t('auth.login_failed');
			}
		} catch (e) {
			reportError(e, { silent: true, category: 'auth' });
			error = i18n.t('auth.login_error');
		} finally {
			submitting = false;
		}
	}
</script>

<div class="login-page">
	<form class="login-card" onsubmit={handleSubmit}>
		<h1 class="login-title">{i18n.t('auth.login')}</h1>

		{#if error}
			<div class="login-error" role="alert">{error}</div>
		{/if}

		<label class="login-field">
			<span class="login-label">{i18n.t('auth.username')}</span>
			<Input
				type="text"
				bind:value={username}
				autocomplete="username"
				required
				disabled={submitting}
			/>
		</label>

		<label class="login-field">
			<span class="login-label">{i18n.t('auth.password')}</span>
			<Input
				type="password"
				bind:value={password}
				autocomplete="current-password"
				required
				disabled={submitting}
			/>
		</label>

		<Button class="login-submit" type="submit" disabled={submitting} loading={submitting}>
			{#if submitting}
				{i18n.t('auth.logging_in')}
			{:else}
				{i18n.t('auth.login')}
			{/if}
		</Button>
	</form>
</div>

<style>
	.login-page {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 100dvh;
		padding: 16px;
		background: var(--bg);
	}

	.login-card {
		width: 100%;
		max-width: 360px;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) * 2);
		padding: 32px 28px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.login-title {
		font-size: 20px;
		font-weight: 600;
		margin: 0 0 4px;
		text-align: center;
		color: var(--text);
	}

	.login-error {
		background: rgba(239, 68, 68, 0.1);
		border: 1px solid var(--danger);
		border-radius: var(--radius);
		padding: 8px 12px;
		font-size: 13px;
		color: var(--danger);
	}

	.login-field {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.login-label {
		font-size: 12px;
		font-weight: 500;
		color: var(--text-muted);
	}

	.login-field input {
		padding: 8px 12px;
		font-size: 14px;
	}

	.login-submit {
		margin-top: 4px;
		padding: 10px 16px;
		background: var(--accent);
		color: #fff;
		font-size: 14px;
		font-weight: 500;
		border-radius: var(--radius);
		transition: background 0.15s;
	}

	.login-submit:hover:not(:disabled) {
		background: var(--accent-hover);
	}

	.login-submit:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
</style>
