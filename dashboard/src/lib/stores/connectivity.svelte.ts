/**
 * Connectivity store: tracks browser online/offline state and SSE connection.
 * Exposes a reactive `isOffline` property used by UI to disable actions.
 */

import { sse } from './sse.svelte.js';

function createConnectivityStore() {
	let browserOnline = $state(typeof navigator !== 'undefined' ? navigator.onLine : true);

	if (typeof window !== 'undefined') {
		window.addEventListener('online', () => {
			browserOnline = true;
		});
		window.addEventListener('offline', () => {
			browserOnline = false;
		});
	}

	return {
		get status(): 'online' | 'reconnecting' | 'offline' {
			if (!browserOnline) return 'offline';
			if (!sse.connected && sse.reconnectCount > 0) return 'reconnecting';
			return 'online';
		},
		get isOffline() {
			return !browserOnline || !sse.connected;
		},
		get browserOnline() {
			return browserOnline;
		},
	};
}

export const connectivity = createConnectivityStore();
