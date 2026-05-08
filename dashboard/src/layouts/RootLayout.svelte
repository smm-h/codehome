<script lang="ts">
	/**
	 * Root application shell.
	 *
	 * Provides the top-level chrome: auth gate, SSE connection, theming,
	 * navigation bar, command palette, context menu, toasts, connection
	 * status dot, update banner, and feature flag loading.
	 */
	import type { Snippet } from 'svelte';
	import favicon from '$lib/assets/favicon.svg';
	import { NavHost, getCoreNavItems, pluginNavItems, buildNavItems, activeItemForPath } from '$lib/nav';
	import { pluginStore } from '$lib/stores/plugins.svelte.js';

	import ToastContainer from '$lib/components/ToastContainer.svelte';
	import CommandPalette from '$lib/components/CommandPalette.svelte';
	import ContextMenu from '$lib/components/ContextMenu.svelte';
	import Button from '$lib/components/Button.svelte';
	import Icon from '$lib/components/Icon.svelte';

	import UpdateBanner from '$lib/components/UpdateBanner.svelte';
	import ConnectionDot from '$lib/components/ConnectionDot.svelte';
	import SkeletonLoader from '$lib/components/SkeletonLoader.svelte';

	import { themeStore } from '$lib/stores/theme.svelte.js';

	import { sse } from '$lib/stores/sse.svelte.js';
	import { auth } from '$lib/stores/auth.svelte.js';
	import { preferences } from '$lib/stores/preferences.svelte.js';
	import { branding } from '$lib/stores/branding.svelte.js';
	import { features } from '$lib/stores/features.svelte.js';
	import { registerActions, type PaletteAction } from '$lib/stores/commandPalette.svelte.js';
	import { toast } from '$lib/stores/toast.svelte.js';
	import { i18n } from '$lib/i18n/index.svelte.js';
	import { goto, page } from '$lib/router/router.svelte.js';
	import { reportError } from '$lib/errors';

	interface Props {
		children: Snippet;
	}

	let { children }: Props = $props();

	let commandPaletteOpen = $state(false);

	// Detect if we're inside a /branch/[repo]/[branch] route
	const isBranchRoute = $derived((page.url.pathname as string).startsWith('/branch/'));

	const navItems = $derived(buildNavItems(getCoreNavItems(), pluginNavItems(pluginStore.plugins)));
	const activeItem = $derived(activeItemForPath(page.url.pathname as string, navItems));

	import { onMount } from 'svelte';

	// Check auth on mount, then redirect if needed.
	onMount(async () => {
		await auth.checkAuth();
		const path = page.url.pathname as string;
		if (!auth.isAuthenticated && path !== '/login') {
			goto('/login');
		} else if (auth.isAuthenticated && path === '/login') {
			goto('/');
		} else if (auth.isAuthenticated) {
			// Load user preferences and team branding once authenticated.
			preferences.load();
			branding.load();
			features.load();
		}
	});

	// SSE lifecycle is managed in App.svelte (app-level singleton),
	// not here, to avoid disconnect/reconnect on route transitions.

	// Sync theme and language from server preferences once loaded.
	$effect(() => {
		if (preferences.loaded) {
			themeStore.setColorScheme(preferences.theme);
			i18n.setLang(preferences.language);
		}
	});

	let prevConnected: boolean | null = $state(null);
	$effect(() => {
		const current = sse.connected;
		if (prevConnected === null) {
			prevConnected = current;
			return;
		}
		if (prevConnected && !current) {
			toast.error('Server disconnected');
		} else if (!prevConnected && current) {
			toast.success('Back online');
		}
		prevConnected = current;
	});

	// Global keyboard shortcut: Cmd+K / Ctrl+K for command palette
	$effect(() => {
		if (typeof window === 'undefined') return;

		function onKeydown(e: KeyboardEvent) {
			if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
				e.preventDefault();
				commandPaletteOpen = !commandPaletteOpen;
			}
		}
		window.addEventListener('keydown', onKeydown);
		return () => window.removeEventListener('keydown', onKeydown);
	});

	// Register global command palette actions once on mount.
	// Uses available() predicates and lazy getters for dynamic context,
	// so no re-registration is needed when route/i18n/theme change.
	import { onDestroy } from 'svelte';

	const paletteActions: PaletteAction[] = [
		{
			id: 'global:new-branch',
			get label() {
				return i18n.t('cmd.action_new_branch');
			},
			icon: 'plus',
			category: 'action',
			available: () => true,
			execute: () => toast.info('Coming soon'),
		},
		{
			id: 'global:refresh',
			get label() {
				return i18n.t('cmd.action_refresh');
			},
			icon: 'refresh',
			category: 'action',
			available: () => true,
			execute: () => location.reload(),
		},
		{
			id: 'global:toggle-theme',
			get label() {
				return i18n.t('cmd.action_theme');
			},
			get icon() {
				return themeStore.current === 'dark' ? 'sun' : 'moon';
			},
			category: 'action',
			available: () => true,
			execute: () => themeStore.toggle(),
		},
		{
			id: 'branch:push',
			get label() {
				return i18n.t('cmd.action_push');
			},
			icon: 'upload',
			category: 'action',
			available: () => isBranchRoute,
			execute: () => toast.info('Use Git tab'),
		},
		{
			id: 'branch:view-changes',
			get label() {
				return i18n.t('cmd.action_view_changes');
			},
			icon: 'git-branch',
			category: 'navigation',
			available: () => {
				if (!isBranchRoute) return false;
				const segments = (page.url.pathname as string).split('/');
				return !!(segments[2] && segments[3]);
			},
			execute: () => {
				const segments = (page.url.pathname as string).split('/');
				goto(`/branch/${segments[2]}/${segments[3]}/git`);
			},
		},
	];

	const unregisterActions = registerActions(paletteActions);
	onDestroy(unregisterActions);

	async function handleLogout() {
		await auth.logout();
		goto('/login');
	}

	const isLoginPage = $derived((page.url.pathname as string) === '/login');
</script>

<svelte:head>
	<title>{branding.productName}</title>
	<link rel="icon" href={favicon} />
</svelte:head>

<!-- Toast container is always rendered (login page too) -->
<ToastContainer />
<ContextMenu />

{#if auth.loading}
	<div class="loading-screen">
		<div class="loading-skeleton">
			<SkeletonLoader variant="lines" />
		</div>
	</div>
{:else if isLoginPage && !auth.isAuthenticated}
	{@render children()}
{:else if auth.isAuthenticated}
	<div class="app-shell">
		<UpdateBanner />
		{#if !isBranchRoute}
			<div class="top-bar">
				<div class="top-bar-nav">
					<NavHost
						items={navItems}
						activeItem={activeItem}
						onNavigate={(path) => goto(path)}
					/>
				</div>
				<div class="top-bar-user">
					<ConnectionDot />
					<span class="user-name">{auth.user?.username}</span>
					<Button
						variant="ghost"
						size="sm"
						class="settings-btn"
						onclick={() => goto('/admin')}
						aria-label={i18n.t('nav.settings')}
						title={i18n.t('nav.settings')}
					>
						<Icon name="settings" size={14} />
					</Button>
					<Button
						variant="ghost"
						size="sm"
						class="logout-btn"
						onclick={handleLogout}
						aria-label={i18n.t('auth.logout')}
					>
						{i18n.t('auth.logout')}
					</Button>
				</div>
			</div>
		{/if}

		<main class="content" class:content-flush={isBranchRoute}>
			{@render children()}
		</main>
	</div>

	<CommandPalette bind:open={commandPaletteOpen} onClose={() => (commandPaletteOpen = false)} />
{:else}
	<div class="loading-screen">
		<div class="loading-skeleton">
			<SkeletonLoader variant="lines" />
		</div>
	</div>
{/if}

<style>
	.loading-screen {
		height: 100dvh;
		background: var(--bg);
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.loading-skeleton {
		width: 280px;
	}

	.app-shell {
		display: flex;
		flex-direction: column;
		height: 100dvh;
		overflow: hidden;
	}

	.top-bar {
		display: flex;
		align-items: center;
		background: var(--bg-surface);
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	.top-bar-nav {
		flex: 1;
		overflow: hidden;
	}

	.top-bar-user {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 0 12px;
		flex-shrink: 0;
	}

	.user-name {
		font-size: 12px;
		color: var(--text-muted);
	}

	.settings-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		border-radius: var(--radius);
		color: var(--text-dim);
		transition:
			color 0.15s,
			background 0.15s;
	}

	.settings-btn:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.logout-btn {
		font-size: 12px;
		color: var(--text-dim);
		padding: 4px 8px;
		border-radius: var(--radius);
		transition:
			color 0.15s,
			background 0.15s;
	}

	.logout-btn:hover {
		color: var(--text);
		background: var(--bg-hover);
	}

	.content {
		flex: 1;
		overflow-y: auto;
		padding: 16px;
	}

	/* Branch routes manage their own padding via the branch layout,
	   so remove the default content padding to avoid double-padding
	   and the gap at the bottom caused by the negative-margin offset. */
	.content-flush {
		padding: 0;
	}
</style>
