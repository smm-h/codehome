class ApiError extends Error {
	constructor(
		public status: number,
		message: string,
	) {
		super(message);
	}
}

// Read a cookie value by name from document.cookie.
function getCookie(name: string): string | undefined {
	return document.cookie
		.split('; ')
		.find((c) => c.startsWith(`${name}=`))
		?.split('=')[1];
}

// Methods that mutate state and require a CSRF token header.
const _CSRF_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/** Default fetch timeout in milliseconds. With the 202 async pattern,
 *  responses should be near-instant, so 15s is very generous. */
const FETCH_TIMEOUT_MS = 15_000;

async function request<T = unknown>(method: string, url: string, body?: unknown): Promise<T> {
	const headers: Record<string, string> = {};
	if (body !== undefined) {
		headers['Content-Type'] = 'application/json';
	}

	// Double-submit CSRF: echo the csrf_token cookie back as a header
	// so the server can verify the two values match.
	if (_CSRF_METHODS.has(method)) {
		const csrfToken = getCookie('csrf_token');
		if (csrfToken) {
			headers['X-CSRF-Token'] = csrfToken;
		}
	}

	const controller = new AbortController();
	const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

	let resp: Response;
	try {
		resp = await fetch(url, {
			method,
			headers,
			body: body !== undefined ? JSON.stringify(body) : undefined,
			credentials: 'include', // send session cookie
			signal: controller.signal,
		});
	} catch (err) {
		// Distinguish abort (timeout) from network errors.
		if (err instanceof DOMException && err.name === 'AbortError') {
			throw new ApiError(0, 'Request timed out');
		}
		throw err;
	} finally {
		clearTimeout(timeoutId);
	}

	if (!resp.ok) {
		const raw = await resp.text().catch(() => resp.statusText);
		// FastAPI returns {"detail": "..."} for HTTPException -- extract the
		// human-readable message instead of showing raw JSON to the user.
		let detail = raw;
		if (raw) {
			try {
				const parsed = JSON.parse(raw);
				if (typeof parsed.detail === 'string') {
					detail = parsed.detail;
				} else if (typeof parsed.message === 'string') {
					detail = parsed.message;
				} else if (typeof parsed.error === 'string') {
					detail = parsed.error;
				}
			} catch {
				// Not JSON -- use the raw text as-is.
			}
		}
		throw new ApiError(resp.status, detail || `HTTP ${resp.status}`);
	}

	// Handle empty responses (204, etc.)
	const text = await resp.text();
	return (text ? JSON.parse(text) : null) as unknown as T;
}

// Upload a FormData body (multipart/form-data). Content-Type is set by
// the browser so the boundary is included automatically.
async function upload<T = unknown>(method: string, url: string, body: FormData): Promise<T> {
	const headers: Record<string, string> = {};

	const csrfToken = getCookie('csrf_token');
	if (csrfToken) {
		headers['X-CSRF-Token'] = csrfToken;
	}

	const resp = await fetch(url, {
		method,
		headers,
		body,
		credentials: 'include',
	});

	if (!resp.ok) {
		const raw = await resp.text().catch(() => resp.statusText);
		let detail = raw;
		if (raw) {
			try {
				const parsed = JSON.parse(raw);
				if (typeof parsed.detail === 'string') detail = parsed.detail;
				else if (typeof parsed.message === 'string') detail = parsed.message;
				else if (typeof parsed.error === 'string') detail = parsed.error;
			} catch {
				// Not JSON.
			}
		}
		throw new ApiError(resp.status, detail || `HTTP ${resp.status}`);
	}

	const text = await resp.text();
	return (text ? JSON.parse(text) : null) as unknown as T;
}

export const api = {
	get: <T = unknown>(url: string) => request<T>('GET', url),
	post: <T = unknown>(url: string, body?: unknown) => request<T>('POST', url, body),
	put: <T = unknown>(url: string, body?: unknown) => request<T>('PUT', url, body),
	patch: <T = unknown>(url: string, body?: unknown) => request<T>('PATCH', url, body),
	del: <T = unknown>(url: string, body?: unknown) => request<T>('DELETE', url, body),
	upload: <T = unknown>(url: string, body: FormData) => upload<T>('POST', url, body),
};

export { ApiError };
