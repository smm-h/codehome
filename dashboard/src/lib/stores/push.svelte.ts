/**
 * Push notification subscription store.
 *
 * Manages the browser Push API subscription lifecycle:
 * - Requests notification permission
 * - Subscribes to push via the service worker
 * - Sends the subscription to the backend
 * - Tracks permission state reactively
 */

import { api } from '$lib/api.js';
import { reportError } from '$lib/errors';

function createPushStore() {
	let permission = $state<NotificationPermission>(
		typeof Notification !== 'undefined' ? Notification.permission : 'default',
	);
	let subscribed = $state(false);
	let loading = $state(false);

	/** Fetch the VAPID public key from the backend. */
	async function getVapidKey(): Promise<string> {
		const resp = await api.get<{ public_key: string }>('/api/push/vapid-key');
		return resp.public_key;
	}

	/** Convert a URL-safe base64 string to a Uint8Array for PushManager. */
	function urlBase64ToUint8Array(base64String: string): Uint8Array {
		const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
		const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
		const rawData = atob(base64);
		const outputArray = new Uint8Array(rawData.length);
		for (let i = 0; i < rawData.length; i++) {
			outputArray[i] = rawData.charCodeAt(i);
		}
		return outputArray;
	}

	/** Request permission, subscribe via PushManager, and register with the backend. */
	async function subscribe(): Promise<boolean> {
		if (typeof Notification === 'undefined' || !('serviceWorker' in navigator)) {
			return false;
		}

		loading = true;
		try {
			// Request notification permission.
			const result = await Notification.requestPermission();
			permission = result;
			if (result !== 'granted') {
				return false;
			}

			// Get the service worker registration.
			const registration = await navigator.serviceWorker.ready;

			// Get VAPID public key from backend.
			const vapidKey = await getVapidKey();
			const applicationServerKey = urlBase64ToUint8Array(vapidKey);

			// Subscribe via the browser Push API.
			// Cast to ArrayBuffer to satisfy the strict BufferSource type expected
			// by PushManager.subscribe (Uint8Array's buffer may be SharedArrayBuffer).
			const pushSubscription = await registration.pushManager.subscribe({
				userVisibleOnly: true,
				applicationServerKey: applicationServerKey.buffer as ArrayBuffer,
			});

			// Send the subscription to our backend.
			const subJson = pushSubscription.toJSON();
			await api.post('/api/push/subscribe', {
				endpoint: subJson.endpoint,
				keys: subJson.keys,
			});

			subscribed = true;
			return true;
		} catch (e) {
			reportError(e, { category: 'push' });
			return false;
		} finally {
			loading = false;
		}
	}

	/** Unsubscribe from push notifications. */
	async function unsubscribe(): Promise<void> {
		if (!('serviceWorker' in navigator)) return;

		loading = true;
		try {
			const registration = await navigator.serviceWorker.ready;
			const pushSubscription = await registration.pushManager.getSubscription();
			if (pushSubscription) {
				// Tell backend to remove this subscription.
				await api.del('/api/push/subscribe', {
					endpoint: pushSubscription.endpoint,
				});
				await pushSubscription.unsubscribe();
			}
			subscribed = false;
		} finally {
			loading = false;
		}
	}

	/** Check if we already have an active push subscription. */
	async function checkSubscription(): Promise<void> {
		if (!('serviceWorker' in navigator)) return;
		try {
			const registration = await navigator.serviceWorker.ready;
			const pushSubscription = await registration.pushManager.getSubscription();
			subscribed = pushSubscription !== null;
			if (typeof Notification !== 'undefined') {
				permission = Notification.permission;
			}
		} catch (e) {
			reportError(e, { silent: true, category: 'push' });
			// Silently ignore -- push not available.
		}
	}

	return {
		get permission() {
			return permission;
		},
		get subscribed() {
			return subscribed;
		},
		get loading() {
			return loading;
		},
		subscribe,
		unsubscribe,
		checkSubscription,
	};
}

export const pushStore = createPushStore();
