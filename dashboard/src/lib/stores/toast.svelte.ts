import { friendlyMessage } from '$lib/errors/messages.js';

interface Toast {
	id: number;
	level: 'error' | 'warning' | 'info' | 'success';
	message: string;
	details?: string;
}

function createToastStore() {
	let toasts = $state<Toast[]>([]);
	let nextId = 0;

	function add(level: Toast['level'], message: unknown, duration = 5000) {
		const id = nextId++;
		let text: string;
		if (typeof message === 'string') {
			text = message;
		} else if (message instanceof Error) {
			text = message.message;
		} else if (message && typeof message === 'object') {
			const obj = message as Record<string, unknown>;
			text =
				typeof obj.detail === 'string'
					? obj.detail
					: typeof obj.message === 'string'
						? obj.message
						: typeof obj.error === 'string'
							? obj.error
							: JSON.stringify(message);
		} else {
			text = String(message);
		}

		let details: string | undefined;
		if (level === 'error') {
			const mapped = friendlyMessage(text);
			if (mapped) {
				details = mapped.raw;
				text = mapped.friendly;
			}
		}

		toasts = [...toasts, { id, level, message: text, details }];
		if (duration > 0) {
			setTimeout(() => dismiss(id), duration);
		}
	}

	function dismiss(id: number) {
		toasts = toasts.filter((t) => t.id !== id);
	}

	return {
		get toasts() {
			return toasts;
		},
		add,
		dismiss,
		error: (msg: unknown) => add('error', msg),
		warning: (msg: unknown) => add('warning', msg),
		info: (msg: unknown) => add('info', msg),
		success: (msg: unknown) => add('success', msg),
	};
}

export const toast = createToastStore();
export type { Toast };
