<script lang="ts">
	import {
		createHighlighter,
		type Highlighter,
		type BundledLanguage,
		type BundledTheme,
		bundledLanguages,
	} from 'shiki';
	import { themeStore } from '$lib/stores/theme.svelte.js';
	import { reportError } from '$lib/errors';

	type DiffLineType = 'added' | 'removed' | 'modified';

	interface Props {
		content: string;
		language: string;
		filename?: string;
		/** Optional per-line diff annotations: line number (1-based) -> type. */
		diffLines?: Map<number, DiffLineType>;
	}

	let { content, language, filename: _filename, diffLines }: Props = $props();

	// Size threshold: skip Shiki for files over 50 KB to avoid UI stalls.
	const SIZE_THRESHOLD = 50 * 1024;

	let highlightedHtml = $state('');
	let highlightLoading = $state(true);
	let _highlightError = $state(false);

	// Lazy singleton highlighter -- initialized once, grammars loaded on demand.
	let highlighterPromise: Promise<Highlighter> | null = null;

	function getHighlighter(): Promise<Highlighter> {
		if (!highlighterPromise) {
			highlighterPromise = createHighlighter({
				themes: ['vitesse-dark', 'vitesse-light'],
				langs: [], // grammars loaded on demand per file
			});
		}
		return highlighterPromise;
	}

	/** Resolve the language ID to use with Shiki. Returns null if unsupported. */
	function resolveLanguage(lang: string): BundledLanguage | null {
		if (!lang) return null;
		const lower = lang.toLowerCase();
		if (lower in bundledLanguages) return lower as BundledLanguage;
		return null;
	}

	/** Pick the Shiki theme matching the current dashboard theme. */
	function currentTheme(): BundledTheme {
		return themeStore.current === 'dark' ? 'vitesse-dark' : 'vitesse-light';
	}

	// Monotonic counter to prevent stale async highlights from overwriting
	// the current result when the user rapidly switches files.
	let highlightGeneration = 0;

	async function highlight() {
		const gen = ++highlightGeneration;
		highlightLoading = true;
		_highlightError = false;
		highlightedHtml = '';

		// Skip Shiki for large files -- render plain text instead.
		if (content.length > SIZE_THRESHOLD) {
			highlightLoading = false;
			return;
		}

		const lang = resolveLanguage(language);
		if (lang === null) {
			// No supported language -- fall back to plain text.
			highlightLoading = false;
			return;
		}

		try {
			const hl = await getHighlighter();
			if (gen !== highlightGeneration) return; // stale

			// Load the grammar on demand if not yet loaded.
			const loaded = hl.getLoadedLanguages();
			if (!loaded.includes(lang)) {
				await hl.loadLanguage(lang);
			}
			if (gen !== highlightGeneration) return; // stale

			highlightedHtml = hl.codeToHtml(content, {
				lang,
				theme: currentTheme(),
			});
		} catch (e) {
			if (gen !== highlightGeneration) return;
			reportError(e, { silent: true, category: 'rendering' });
			_highlightError = true;
		} finally {
			if (gen === highlightGeneration) {
				highlightLoading = false;
			}
		}
	}

	/**
	 * Post-process Shiki HTML to inject diff-type classes on individual
	 * <span class="line"> elements.  Each line span gets an additional
	 * class like `diff-added`, `diff-removed`, or `diff-modified` which
	 * CSS then styles with colored backgrounds and gutter markers.
	 */
	function injectDiffClasses(html: string, diffs: Map<number, DiffLineType>): string {
		if (!diffs || diffs.size === 0) return html;
		let lineNum = 0;
		return html.replace(/<span class="line"/g, () => {
			lineNum++;
			const type = diffs.get(lineNum);
			if (type) {
				return `<span class="line diff-${type}"`;
			}
			return '<span class="line"';
		});
	}

	// Re-highlight when content, language, or theme changes.
	$effect(() => {
		// Access reactive deps explicitly so Svelte tracks them.
		void content;
		void language;
		void themeStore.current;
		highlight();
	});

	/** Shiki HTML with diff classes injected (if applicable). */
	let displayHtml = $derived(
		highlightedHtml && diffLines ? injectDiffClasses(highlightedHtml, diffLines) : highlightedHtml,
	);

	/** Split plain-text content into lines for per-line diff rendering. */
	let plainLines = $derived(content.split('\n'));
</script>

{#if highlightLoading}
	<div class="code-loading">
		<div class="code-loading-skeleton"></div>
	</div>
{:else if displayHtml}
	<div
		class="code-highlighted"
		class:line-numbers={true}
		class:has-diff={diffLines && diffLines.size > 0}
	>
		<!-- eslint-disable-next-line svelte/no-at-html-tags -- Shiki codeToHtml output; generated locally by our own highlighter, not user input -->
		{@html displayHtml}
	</div>
{:else}
	<!-- Plain text fallback: unsupported language, large file, or Shiki error. -->
	<pre class="code-plain" class:has-diff={diffLines && diffLines.size > 0}><code
			>{#each plainLines as line, idx (idx)}{@const lineNum = idx + 1}{@const diffType =
					diffLines?.get(lineNum)}<span class="plain-line{diffType ? ` diff-${diffType}` : ''}"
					>{line}
</span>{/each}</code
		></pre>
{/if}

<style>
	/* Loading skeleton while Shiki initializes. */
	.code-loading {
		flex: 1;
		padding: 12px 16px;
		background: var(--bg);
	}

	.code-loading-skeleton {
		width: 60%;
		height: 120px;
		background: var(--bg-surface);
		border-radius: 4px;
		animation: pulse 1.5s ease-in-out infinite;
	}

	@keyframes pulse {
		0%,
		100% {
			opacity: 0.6;
		}
		50% {
			opacity: 1;
		}
	}

	/* Container for Shiki-highlighted HTML. */
	.code-highlighted {
		flex: 1;
		overflow: auto;
		font-size: 12px;
		line-height: 1.6;
		tab-size: 4;
	}

	/* Override Shiki's <pre> to fill the container and respect our layout. */
	.code-highlighted :global(pre.shiki) {
		margin: 0;
		padding: 12px 0;
		overflow: visible;
		font-family: var(--font-mono, monospace);
		border-radius: 0;
	}

	.code-highlighted :global(pre.shiki code) {
		display: block;
		counter-reset: line;
	}

	/* Line numbers via CSS counters -- avoids polluting Shiki's HTML output. */
	.code-highlighted.line-numbers :global(pre.shiki code .line) {
		display: inline-block;
		width: 100%;
		padding-left: 16px;
	}

	.code-highlighted.line-numbers :global(pre.shiki code .line::before) {
		counter-increment: line;
		content: counter(line);
		display: inline-block;
		width: 3.5em;
		margin-right: 16px;
		text-align: right;
		color: var(--text-dim, #555);
		user-select: none;
		-webkit-user-select: none;
		opacity: 0.5;
	}

	/* Plain text fallback (no highlighting). */
	.code-plain {
		flex: 1;
		overflow: auto;
		margin: 0;
		padding: 12px 16px;
		font-size: 12px;
		line-height: 1.6;
		tab-size: 4;
		font-family: var(--font-mono, monospace);
		background: var(--bg);
		color: var(--text);
	}

	.code-plain code {
		display: block;
		white-space: pre;
	}

	/* -- Diff highlighting: colored backgrounds + gutter markers ---------- */

	/* Shiki highlighted lines with diff annotations. */
	.code-highlighted :global(.line.diff-added) {
		background: rgba(46, 160, 67, 0.15);
	}
	.code-highlighted :global(.line.diff-removed) {
		background: rgba(248, 81, 73, 0.15);
	}
	.code-highlighted :global(.line.diff-modified) {
		background: rgba(227, 179, 65, 0.15);
	}

	/* Gutter markers for diff lines (override the line-number counter). */
	.code-highlighted.has-diff :global(.line.diff-added::before) {
		content: '+' !important;
		color: rgb(46, 160, 67);
		opacity: 1;
	}
	.code-highlighted.has-diff :global(.line.diff-removed::before) {
		content: '-' !important;
		color: rgb(248, 81, 73);
		opacity: 1;
	}
	.code-highlighted.has-diff :global(.line.diff-modified::before) {
		content: '~' !important;
		color: rgb(227, 179, 65);
		opacity: 1;
	}

	/* Plain text fallback: per-line diff coloring. */
	.code-plain .plain-line {
		display: block;
	}
	.code-plain .plain-line.diff-added {
		background: rgba(46, 160, 67, 0.15);
	}
	.code-plain .plain-line.diff-removed {
		background: rgba(248, 81, 73, 0.15);
	}
	.code-plain .plain-line.diff-modified {
		background: rgba(227, 179, 65, 0.15);
	}

	/* Plain text line numbers when diff is active. */
	.code-plain.has-diff {
		counter-reset: plain-line;
	}
	.code-plain.has-diff .plain-line {
		padding-left: 16px;
	}
	.code-plain.has-diff .plain-line::before {
		counter-increment: plain-line;
		content: counter(plain-line);
		display: inline-block;
		width: 3.5em;
		margin-right: 16px;
		text-align: right;
		color: var(--text-dim, #555);
		user-select: none;
		-webkit-user-select: none;
		opacity: 0.5;
	}
	.code-plain.has-diff .plain-line.diff-added::before {
		content: '+';
		color: rgb(46, 160, 67);
		opacity: 1;
	}
	.code-plain.has-diff .plain-line.diff-removed::before {
		content: '-';
		color: rgb(248, 81, 73);
		opacity: 1;
	}
	.code-plain.has-diff .plain-line.diff-modified::before {
		content: '~';
		color: rgb(227, 179, 65);
		opacity: 1;
	}
</style>
