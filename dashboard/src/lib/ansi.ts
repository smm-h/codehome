/**
 * ANSI escape sequence parser.
 *
 * Converts ANSI-colored terminal output to either:
 *   - HTML spans with inline styles (for the full-text terminal view)
 *   - Per-character color data (for the canvas minimap)
 *
 * Handles SGR codes: reset (0), bold (1), standard fg (30-37),
 * standard bg (40-47), bright fg (90-97), bright bg (100-107),
 * and 256-color mode (38;5;N / 48;5;N).
 */

// Standard 8-color palette (normal + bright variants).
const COLORS_16: string[] = [
	'#000000', // 0 black
	'#cc0000', // 1 red
	'#4e9a06', // 2 green
	'#c4a000', // 3 yellow
	'#3465a4', // 4 blue
	'#75507b', // 5 magenta
	'#06989a', // 6 cyan
	'#d3d7cf', // 7 white
	'#555753', // 8 bright black
	'#ef2929', // 9 bright red
	'#8ae234', // 10 bright green
	'#fce94f', // 11 bright yellow
	'#729fcf', // 12 bright blue
	'#ad7fa8', // 13 bright magenta
	'#34e2e2', // 14 bright cyan
	'#eeeeec', // 15 bright white
];

/** Resolve a 256-color index to an RGB hex string. */
function color256(n: number): string {
	if (n < 16) return COLORS_16[n] ?? '#d3d7cf';
	if (n < 232) {
		// 6x6x6 color cube: index 16-231
		const idx = n - 16;
		const r = Math.floor(idx / 36);
		const g = Math.floor((idx % 36) / 6);
		const b = idx % 6;
		const toHex = (v: number) => (v === 0 ? 0 : 55 + v * 40).toString(16).padStart(2, '0');
		return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
	}
	// Grayscale ramp: index 232-255
	const level = 8 + (n - 232) * 10;
	const hex = level.toString(16).padStart(2, '0');
	return `#${hex}${hex}${hex}`;
}

/** Regex matching a single SGR escape sequence (e.g. \x1b[31m). */
// eslint-disable-next-line no-control-regex -- ANSI parsing legitimately matches the escape character.
const SGR_RE = /\x1b\[([0-9;]*)m/g;

/**
 * Strip all non-SGR ANSI escape sequences so they don't leak as garbled text.
 *
 * Matches CSI sequences that end in something other than 'm' (cursor movement,
 * erase, scroll, etc.), OSC sequences (window titles), and character-set
 * selection escapes. SGR sequences (\x1b[...m) are preserved.
 */
// eslint-disable-next-line no-control-regex -- ANSI parsing legitimately matches the escape character.
const NON_SGR_RE = /\x1b\[[0-9;]*[A-HJKSTfhlnr]|\x1b\].*?(?:\x07|\x1b\\)|\x1b[()][AB012]/g;

function stripNonSgr(input: string): string {
	return input.replace(NON_SGR_RE, '');
}

interface AnsiState {
	fg: string | null;
	bg: string | null;
	bold: boolean;
}

/** Parse SGR parameter codes and mutate state accordingly. */
function applySgr(params: number[], state: AnsiState): void {
	let i = 0;
	while (i < params.length) {
		const code = params[i];
		if (code === 0) {
			// Reset all attributes.
			state.fg = null;
			state.bg = null;
			state.bold = false;
		} else if (code === 1) {
			state.bold = true;
		} else if (code === 22) {
			state.bold = false;
		} else if (code >= 30 && code <= 37) {
			// Standard foreground colors. Bold shifts to bright variant.
			state.fg = state.bold ? COLORS_16[code - 30 + 8] : COLORS_16[code - 30];
		} else if (code === 39) {
			state.fg = null; // default fg
		} else if (code >= 40 && code <= 47) {
			state.bg = COLORS_16[code - 40];
		} else if (code === 49) {
			state.bg = null; // default bg
		} else if (code >= 90 && code <= 97) {
			state.fg = COLORS_16[code - 90 + 8]; // bright fg
		} else if (code >= 100 && code <= 107) {
			state.bg = COLORS_16[code - 100 + 8]; // bright bg
		} else if (code === 38 && params[i + 1] === 5) {
			// 256-color foreground: 38;5;N
			state.fg = color256(params[i + 2] ?? 0);
			i += 2;
		} else if (code === 48 && params[i + 1] === 5) {
			// 256-color background: 48;5;N
			state.bg = color256(params[i + 2] ?? 0);
			i += 2;
		}
		i++;
	}
}

/**
 * Convert an ANSI-colored string to HTML with inline-styled spans.
 *
 * Characters are HTML-escaped. Consecutive characters with the same
 * style share a single <span>.
 */
export function ansiToHtml(input: string): string {
	input = stripNonSgr(input);
	const state: AnsiState = { fg: null, bg: null, bold: false };
	const parts: string[] = [];
	let lastIndex = 0;

	SGR_RE.lastIndex = 0;
	let match: RegExpExecArray | null;

	while ((match = SGR_RE.exec(input)) !== null) {
		// Emit text before this escape sequence.
		if (match.index > lastIndex) {
			const text = input.slice(lastIndex, match.index);
			parts.push(styledSpan(text, state));
		}
		// Parse and apply the SGR codes.
		const codes = match[1] ? match[1].split(';').map(Number) : [0];
		applySgr(codes, state);
		lastIndex = SGR_RE.lastIndex;
	}

	// Emit any remaining text after the last escape sequence.
	if (lastIndex < input.length) {
		parts.push(styledSpan(input.slice(lastIndex), state));
	}

	return parts.join('');
}

/** Build an HTML span (or plain text) for a chunk with given ANSI state. */
function styledSpan(text: string, state: AnsiState): string {
	const escaped = escapeHtml(text);
	if (!state.fg && !state.bg && !state.bold) return escaped;

	const styles: string[] = [];
	if (state.fg) styles.push(`color:${state.fg}`);
	if (state.bg) styles.push(`background:${state.bg}`);
	if (state.bold) styles.push('font-weight:bold');
	return `<span style="${styles.join(';')}">${escaped}</span>`;
}

function escapeHtml(s: string): string {
	return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** Per-character color info for the canvas minimap. */
export interface CharColor {
	char: string;
	fg: string;
	bg: string | null;
}

/**
 * Convert an ANSI-colored string to per-character color data.
 *
 * Used by the minimap canvas to render each character as a colored pixel.
 * The default foreground is provided by the caller (typically a light gray).
 */
export function ansiToColors(input: string, defaultFg = '#d3d7cf'): CharColor[] {
	input = stripNonSgr(input);
	const state: AnsiState = { fg: null, bg: null, bold: false };
	const result: CharColor[] = [];
	let lastIndex = 0;

	SGR_RE.lastIndex = 0;
	let match: RegExpExecArray | null;

	while ((match = SGR_RE.exec(input)) !== null) {
		// Emit characters before this escape.
		for (let i = lastIndex; i < match.index; i++) {
			result.push({
				char: input[i],
				fg: state.fg ?? defaultFg,
				bg: state.bg,
			});
		}
		const codes = match[1] ? match[1].split(';').map(Number) : [0];
		applySgr(codes, state);
		lastIndex = SGR_RE.lastIndex;
	}

	// Remaining characters after the last escape.
	for (let i = lastIndex; i < input.length; i++) {
		result.push({
			char: input[i],
			fg: state.fg ?? defaultFg,
			bg: state.bg,
		});
	}

	return result;
}
