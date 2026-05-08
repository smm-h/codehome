/**
 * Reactive store for tracking long-running operation progress.
 *
 * Subscribes to `operation.progress` SSE events and exposes a live map of
 * active operations keyed by operation_id.  Completed (success) operations
 * auto-remove after a short delay; error operations persist until dismissed.
 */
import { SvelteMap, SvelteSet } from 'svelte/reactivity';
import { sse } from './sse.svelte.js';

export interface Operation {
	operation_id: string;
	service_key: string;
	label: string;
	phase: string;
	determinate: boolean;
	progress: number | null;
	done: boolean;
	error: string | null;
}

/** Delay (ms) before a successfully completed operation is removed from the map. */
const SUCCESS_REMOVE_DELAY = 5_000;

function createOperationStore() {
	const ops: SvelteMap<string, Operation> = new SvelteMap();

	// Pending auto-removal timers keyed by operation_id, so we can cancel
	// them if the operation reappears or is manually dismissed.
	// SvelteMap used to satisfy the svelte/prefer-svelte-reactivity lint rule,
	// though this map is internal bookkeeping and not read in templates.
	const removalTimers = new SvelteMap<string, ReturnType<typeof setTimeout>>();

	// Track operation IDs we've already seen so we can detect new ones.
	const seenIds = new SvelteSet<string>();

	// Reactive: the most recently started operation. Components can watch
	// this to open a drawer/panel when a new operation begins.
	let latestStarted = $state<Operation | null>(null);

	// Set of operation IDs for which the drawer was dismissed by the user.
	// Tracked so the ServiceCard can show a small inline indicator.
	const dismissed = new SvelteMap<string, boolean>();

	sse.subscribe('operation.progress', (data) => {
		const op = data as unknown as Operation;

		// Cancel any pending removal for this operation (e.g. server sent a
		// new update for an id we were about to evict).
		const existing = removalTimers.get(op.operation_id);
		if (existing) {
			clearTimeout(existing);
			removalTimers.delete(op.operation_id);
		}

		// Detect a brand-new operation (first progress event for this id).
		if (!seenIds.has(op.operation_id)) {
			seenIds.add(op.operation_id);
			latestStarted = op;
		}

		ops.set(op.operation_id, op);

		// Schedule auto-removal for successful completions.
		if (op.done && !op.error) {
			const timer = setTimeout(() => {
				ops.delete(op.operation_id);
				removalTimers.delete(op.operation_id);
				dismissed.delete(op.operation_id);
			}, SUCCESS_REMOVE_DELAY);
			removalTimers.set(op.operation_id, timer);
		}
	});

	/** Return all active operations for a given service key. */
	function getByServiceKey(serviceKey: string): Operation[] {
		const result: Operation[] = [];
		for (const op of ops.values()) {
			if (op.service_key === serviceKey) {
				result.push(op);
			}
		}
		return result;
	}

	/** Dismiss an error operation (or any operation) by id. */
	function dismiss(operationId: string) {
		const timer = removalTimers.get(operationId);
		if (timer) {
			clearTimeout(timer);
			removalTimers.delete(operationId);
		}
		ops.delete(operationId);
	}

	/** Mark an operation's drawer as dismissed (user closed it). */
	function markDismissed(operationId: string) {
		dismissed.set(operationId, true);
	}

	/** Check if an operation's drawer was dismissed. */
	function isDismissed(operationId: string): boolean {
		return dismissed.get(operationId) === true;
	}

	/** Clear the latestStarted signal (after the consumer has acted on it). */
	function consumeLatestStarted(): Operation | null {
		const op = latestStarted;
		latestStarted = null;
		return op;
	}

	return {
		/** The full reactive map -- useful for advanced consumers. */
		get all() {
			return ops;
		},
		/** The most recently started operation (reactive). */
		get latestStarted() {
			return latestStarted;
		},
		getByServiceKey,
		dismiss,
		markDismissed,
		isDismissed,
		consumeLatestStarted,
	};
}

export const operations = createOperationStore();
