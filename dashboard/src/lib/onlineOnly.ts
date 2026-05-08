/**
 * Svelte action that disables a button when the dashboard is offline.
 *
 * Usage: <button use:onlineOnly>Start</button>
 *
 * When offline (or reconnecting):
 *   - Adds an `offline-disabled` CSS class (greyed out, pointer-events: none)
 *   - Shows a tooltip via the `title` attribute
 *   - Intercepts click events to prevent them from firing
 *
 * When back online, restores the original state.
 *
 * NOTE: This action does NOT set the native `disabled` attribute, because
 * that would conflict with Svelte's own reactive `disabled={...}` bindings.
 * Instead it uses CSS and event interception to block interaction.
 */

import type { ActionReturn } from 'svelte/action';

import { i18n } from '$lib/i18n/index.svelte.js';
import { connectivity } from '$lib/stores/connectivity.svelte.js';

function blockClick(e: Event) {
	e.stopImmediatePropagation();
	e.preventDefault();
}

export function onlineOnly(node: HTMLButtonElement): ActionReturn {
	let originalTitle: string | null = null;
	let active = false;

	const cleanup = $effect.root(() => {
		$effect(() => {
			const offline = connectivity.isOffline;

			if (offline && !active) {
				originalTitle = node.getAttribute('title');
				node.classList.add('offline-disabled');
				node.setAttribute('title', i18n.t('connectivity.action_disabled'));
				node.addEventListener('click', blockClick, true);
				active = true;
			} else if (!offline && active) {
				node.classList.remove('offline-disabled');
				if (originalTitle !== null) {
					node.setAttribute('title', originalTitle);
				} else {
					node.removeAttribute('title');
				}
				node.removeEventListener('click', blockClick, true);
				active = false;
			}
		});
	});

	return {
		destroy() {
			if (active) {
				node.removeEventListener('click', blockClick, true);
			}
			cleanup();
		},
	};
}
