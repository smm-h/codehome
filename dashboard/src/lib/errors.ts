import { toast } from '$lib/stores/toast.svelte';

interface ErrorContext {
	/** Don't show a toast -- just persist to the error log. */
	silent?: boolean;
	/** Error category for filtering in the errors page (e.g. 'terminal', 'git', 'service'). */
	category?: string;
}

/**
 * Central error handler. Persists to the backend error log and optionally
 * shows a toast notification. Every catch block should call this.
 */
export function reportError(error: unknown, context?: ErrorContext): void {
	const message = extractMessage(error);

	// Always persist to the backend error log.
	persist(message, error, context);

	// Show a toast unless explicitly silenced.
	if (!context?.silent) {
		toast.error(message);
	}
}

/** Extract a human-readable message from any thrown value. */
function extractMessage(error: unknown): string {
	if (typeof error === 'string') return error;
	if (error instanceof Error) return error.message;
	if (error && typeof error === 'object') {
		const obj = error as Record<string, unknown>;
		if (typeof obj.detail === 'string') return obj.detail;
		if (typeof obj.message === 'string') return obj.message;
		if (typeof obj.error === 'string') return obj.error;
		return JSON.stringify(error);
	}
	return String(error);
}

/** Fire-and-forget POST to the diagnostics endpoint. */
function persist(message: string, error: unknown, context?: ErrorContext): void {
	const stack = error instanceof Error ? (error.stack?.slice(0, 500) ?? '') : '';
	const status =
		error instanceof Error && 'status' in error ? (error as { status: number }).status : 0;

	fetch('/api/diagnostics/errors', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			errors: [
				{
					message,
					source: context?.category ?? 'app',
					line: status,
					stack,
					category: context?.category ?? 'app',
					timestamp: new Date().toISOString(),
				},
			],
		}),
	}).catch(() => {});
}
