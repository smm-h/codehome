/**
 * Branding store -- team-wide settings like product name and default accent.
 *
 * Stored on the server under the "branding" global preference namespace.
 * Any admin can update it; all users read it on load.
 */

import { api } from '$lib/api.js';
import { reportError } from '$lib/errors';
import { toast } from '$lib/stores/toast.svelte.js';
export interface BrandingSettings {
	productName: string;
	defaultAccent: string;
}

const DEFAULTS: BrandingSettings = {
	productName: 'Supervisor',
	defaultAccent: 'indigo',
};

function createBrandingStore() {
	let productName = $state(DEFAULTS.productName);
	let defaultAccent = $state<string>(DEFAULTS.defaultAccent);
	let logoUrl = $state<string | null>(null);
	let loaded = $state(false);

	async function load() {
		try {
			const data = await api.get<Partial<BrandingSettings>>('/api/global-preferences/branding');
			// API returns {} if namespace not set.
			if (data && typeof data === 'object') {
				productName = data.productName ?? DEFAULTS.productName;
				defaultAccent = data.defaultAccent ?? DEFAULTS.defaultAccent;
			}
			loaded = true;
		} catch (e) {
			reportError(e, { silent: true, category: 'branding' });
			loaded = true; // Use defaults on failure.
		}
		// Check if a logo exists (HEAD-like check via GET; 404 = no logo).
		await refreshLogo();
	}

	async function refreshLogo() {
		try {
			const resp = await fetch('/api/branding/logo', { method: 'HEAD' });
			logoUrl = resp.ok ? `/api/branding/logo?t=${Date.now()}` : null;
		} catch (e) {
			reportError(e, { silent: true, category: 'branding' });
			logoUrl = null;
		}
	}

	async function update(settings: Partial<BrandingSettings>) {
		const prev = { productName, defaultAccent };
		// Optimistic update.
		if (settings.productName !== undefined) productName = settings.productName;
		if (settings.defaultAccent !== undefined) defaultAccent = settings.defaultAccent;
		try {
			await api.put('/api/global-preferences/branding', {
				productName,
				defaultAccent,
			});
		} catch (e) {
			// Rollback.
			productName = prev.productName;
			defaultAccent = prev.defaultAccent;
			toast.error(e instanceof Error ? e.message : 'Failed to save branding');
		}
	}

	return {
		get productName() {
			return productName;
		},
		get defaultAccent() {
			return defaultAccent;
		},
		get logoUrl() {
			return logoUrl;
		},
		get loaded() {
			return loaded;
		},
		load,
		update,
		refreshLogo,
	};
}

export const branding = createBrandingStore();
