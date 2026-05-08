import { api } from '$lib/api.js';
import { reportError } from '$lib/errors';
import { toast } from '$lib/stores/toast.svelte.js';

export type NavLayout = 'second-row' | 'dropdown' | 'inline-swap' | 'flat-tabs';
export type PluginDisplayMode = 'chrome' | 'seamless';

function createPreferencesStore() {
	let metricsVariant = $state<'chartjs' | 'sparkline' | 'canvas'>('sparkline');
	let theme = $state<'auto' | 'dark' | 'light'>('auto');
	let language = $state<'en' | 'it'>('en');
	let accentColor = $state<string>('indigo');
	let navLayout = $state<NavLayout>('second-row');
	let pluginDisplayMode = $state<PluginDisplayMode>('chrome');
	let loaded = $state(false);

	async function load() {
		try {
			const prefs = await api.get<{
				metrics_variant?: 'chartjs' | 'sparkline' | 'canvas';
				theme?: 'auto' | 'dark' | 'light';
				language?: 'en' | 'it';
				accent_color?: string;
				nav_layout?: NavLayout;
				plugin_display_mode?: PluginDisplayMode;
			}>('/api/preferences');
			metricsVariant = prefs?.metrics_variant ?? 'sparkline';
			theme = prefs?.theme ?? 'auto';
			language = prefs?.language ?? 'en';
			accentColor = prefs?.accent_color ?? 'indigo';
			navLayout = prefs?.nav_layout ?? 'second-row';
			pluginDisplayMode = prefs?.plugin_display_mode ?? 'chrome';
			loaded = true;
		} catch (e) {
			reportError(e, { silent: true, category: 'preferences' });
			// Silently ignore load failure -- defaults remain in place.
		}
	}

	/** Optimistically update local state, then persist to server.
	 *  If the API call fails, roll back to the previous value. */
	async function update(key: string, value: string) {
		// Save previous values for rollback.
		const prev = {
			metricsVariant,
			theme,
			language,
			accentColor,
			navLayout,
			pluginDisplayMode,
		};

		if (key === 'metrics_variant') metricsVariant = value as typeof metricsVariant;
		if (key === 'theme') theme = value as typeof theme;
		if (key === 'language') language = value as typeof language;
		if (key === 'accent_color') accentColor = value as typeof accentColor;
		if (key === 'nav_layout') navLayout = value as typeof navLayout;
		if (key === 'plugin_display_mode') pluginDisplayMode = value as typeof pluginDisplayMode;

		try {
			await api.put('/api/preferences', { [key]: value });
		} catch (e) {
			// Rollback to previous values.
			metricsVariant = prev.metricsVariant;
			theme = prev.theme;
			language = prev.language;
			accentColor = prev.accentColor;
			navLayout = prev.navLayout;
			pluginDisplayMode = prev.pluginDisplayMode;
			toast.error(e instanceof Error ? e.message : 'Failed to save preference');
		}
	}

	return {
		get metricsVariant() {
			return metricsVariant;
		},
		get theme() {
			return theme;
		},
		get language() {
			return language;
		},
		get accentColor() {
			return accentColor;
		},
		get navLayout() {
			return navLayout;
		},
		get pluginDisplayMode() {
			return pluginDisplayMode;
		},
		get loaded() {
			return loaded;
		},
		load,
		update,
	};
}

export const preferences = createPreferencesStore();
