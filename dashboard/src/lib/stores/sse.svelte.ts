import { SvelteMap } from 'svelte/reactivity';
import { reportError } from '$lib/errors';

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- SSE handlers use specific types at call sites
type EventHandler = (data: any) => void;

/** Fire-and-forget SSE diagnostic beacon to the server. */
function _reportSSE(event: string, data: Record<string, unknown>) {
	try {
		const body = JSON.stringify({ event, ...data, ts: new Date().toISOString() });
		fetch('/api/diagnostics/sse', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body,
			keepalive: true,
		}).catch(() => {});
		console.warn(`[SSE] ${event}`, data);
	} catch {
		// Best-effort
	}
}

function createSSEStore() {
	let connected = $state(false);
	let reconnectCount = $state(0);
	let eventSource: EventSource | null = null;
	let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	const listeners: Map<string, Set<EventHandler>> = new SvelteMap();

	// Track which event types have been registered on the current EventSource
	// so we don't double-register when registerEventTypes() is called.
	// eslint-disable-next-line svelte/prefer-svelte-reactivity -- intentionally non-reactive
	const registeredTypes = new Set<string>();

	// Plugin event types that must survive reconnects. Plain Set (no reactivity
	// needed) -- populated by registerEventTypes(), replayed in connect().
	// eslint-disable-next-line svelte/prefer-svelte-reactivity -- intentionally non-reactive
	const pluginTypes = new Set<string>();

	// Core event types known at compile time.
	const CORE_EVENT_TYPES = [
		'service.state',
		'service.log',
		'service.health',
		'service.metrics',
		'ports.state',
		'tdd.phase',
		'tdd.output',
		'tests.output',
		'tests.run.DONE',
		'branch.switch',
		'agent.state',
		'agent.output',
		'agent.run.DONE',
		'agent.event',
		'agent.question',
		'pipeline.state',
		'plan.state',
		'conductor.message',
		'conductor.start.DONE',
		'conductor.stop',
		'conductor.autonomy',
		'rebase.progress',
		'rebase.DONE',
		'rebase.conflict',
		'design.status',
		'terminal.presence',
		'remote.fetch.DONE',
		'remote.branch.create',
		'remote.branch.delete',
		'operation.progress',
		'operation.output',
		'service.deps',
		'telemac.state',
		'feature.changed',
	];

	/** Attach a listener for a single event type on the current EventSource. */
	function _attachType(type: string) {
		if (!eventSource || registeredTypes.has(type)) return;
		registeredTypes.add(type);
		eventSource.addEventListener(type, (e: MessageEvent) => {
			try {
				const data = JSON.parse(e.data);
				const fns = listeners.get(type);
				if (fns) fns.forEach((fn) => fn(data));
			} catch (e) {
				reportError(e, { silent: true, category: 'sse' });
			}
		});
	}

	function connect(token?: string) {
		if (typeof window === 'undefined') return;

		// If already connected to the same endpoint, skip reconnection.
		// This prevents the "reconnecting" banner flash on SvelteKit navigation,
		// where the layout effect re-runs and calls connect() with the same token.
		const url = token ? `/events?token=${encodeURIComponent(token)}` : '/events';
		if (
			eventSource &&
			eventSource.readyState === EventSource.OPEN &&
			eventSource.url.endsWith(url)
		) {
			return;
		}

		// Disconnect any existing connection before reconnecting.
		disconnect();

		// EventSource cannot send Authorization headers, so pass the JWT
		// as a query param (server accepts ?token= as an auth alternative).
		eventSource = new EventSource(url, { withCredentials: true });

		eventSource.onopen = () => {
			const wasDisconnected = !connected;
			connected = true;
			reconnectCount++;
			if (wasDisconnected && _lastError) {
				const gap = Date.now() - _lastError;
				_reportSSE('reconnected', { gap_ms: gap });
			}
		};

		let _lastError = 0;
		eventSource.onerror = () => {
			_lastError = Date.now();
			const rs = eventSource?.readyState ?? -1;
			_reportSSE('error', { readyState: rs });
			connected = false;
			// Browser gave up retrying (CLOSED state) -- schedule manual reconnect.
			if (eventSource?.readyState === EventSource.CLOSED) {
				reconnectTimer = setTimeout(() => connect(token), 3000);
			}
		};

		// Register all core event types on the fresh EventSource.
		registeredTypes.clear();
		for (const type of CORE_EVENT_TYPES) {
			_attachType(type);
		}
		// Re-register plugin event types that were added via registerEventTypes().
		for (const type of pluginTypes) {
			_attachType(type);
		}
	}

	/**
	 * Register additional SSE event types at runtime.
	 *
	 * Called after plugin discovery to attach listeners for event types
	 * declared by plugins. Safe to call multiple times -- already-registered
	 * types are skipped.
	 */
	function registerEventTypes(types: string[]) {
		for (const type of types) {
			pluginTypes.add(type);
			_attachType(type);
		}
	}

	function disconnect() {
		if (reconnectTimer) {
			clearTimeout(reconnectTimer);
			reconnectTimer = null;
		}
		eventSource?.close();
		eventSource = null;
		connected = false;
	}

	function subscribe(eventType: string, handler: EventHandler): () => void {
		if (!listeners.has(eventType)) listeners.set(eventType, new Set());
		// Safe: the line above guarantees the key exists.
		(listeners.get(eventType) as Set<EventHandler>).add(handler);
		return () => listeners.get(eventType)?.delete(handler);
	}

	return {
		get connected() {
			return connected;
		},
		get reconnectCount() {
			return reconnectCount;
		},
		connect,
		disconnect,
		subscribe,
		registerEventTypes,
	};
}

export const sse = createSSEStore();
