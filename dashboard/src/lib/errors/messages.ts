const ERROR_MAP: Record<string, string> = {
	'Missing CSRF token': 'Session expired. Please refresh the page.',
	'CSRF token mismatch': 'Session expired. Please refresh the page.',
	'Invalid credentials': 'Wrong username or password.',
	'Not authenticated': 'You need to log in.',
	'Invalid or expired token': 'Your session has expired. Please log in again.',
	'Admin access required': 'This action requires admin privileges.',
};

export function friendlyMessage(raw: string): { friendly: string; raw: string } | null {
	for (const [pattern, friendly] of Object.entries(ERROR_MAP)) {
		if (raw.includes(pattern)) {
			return { friendly, raw };
		}
	}
	return null;
}
