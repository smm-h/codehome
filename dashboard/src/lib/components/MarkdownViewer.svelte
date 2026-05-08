<script lang="ts">
	import { marked } from 'marked';
	import {
		createHighlighter,
		type Highlighter,
		type BundledLanguage,
		bundledLanguages,
	} from 'shiki';
	import { themeStore } from '$lib/stores/theme.svelte.js';
	import { reportError } from '$lib/errors';

	interface Props {
		content: string;
	}

	let { content }: Props = $props();

	// Lazy Shiki highlighter singleton, shared with CodeViewer's approach.
	let highlighterPromise: Promise<Highlighter> | null = null;

	function getHighlighter(): Promise<Highlighter> {
		if (!highlighterPromise) {
			highlighterPromise = createHighlighter({
				themes: ['vitesse-dark', 'vitesse-light'],
				langs: [],
			});
		}
		return highlighterPromise;
	}

	function resolveLanguage(lang: string): BundledLanguage | null {
		if (!lang) return null;
		const lower = lang.toLowerCase();
		if (lower in bundledLanguages) return lower as BundledLanguage;
		return null;
	}

	/**
	 * Sanitize HTML to prevent XSS. Strips script tags, event handlers,
	 * javascript: URLs, and other dangerous patterns. This is a pragmatic
	 * allow-list approach for a local dashboard -- not a public-facing app.
	 */
	function sanitizeHtml(html: string): string {
		// Build regex patterns with concatenation to avoid Svelte parsing
		// literal '<script' as a component tag.
		const scriptOpen = '<' + 'script';
		const scriptClose = '</' + 'script>';
		const scriptPattern = new RegExp(
			scriptOpen + '\\b[^<]*(?:(?!' + scriptClose + ')<[^<]*)*' + scriptClose,
			'gi',
		);
		return (
			html
				// Remove script tags and their contents.
				.replace(scriptPattern, '')
				// Remove event handler attributes (onclick, onerror, etc.)
				.replace(/\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
				// Remove javascript: and data: URLs in href/src attributes.
				.replace(/(href|src)\s*=\s*["']?\s*(?:javascript|data)\s*:/gi, '$1="')
				// Remove iframe, object, embed, form tags.
				.replace(/<\/?(iframe|object|embed|form)\b[^>]*>/gi, '')
		);
	}

	let renderedHtml = $state('');
	let rendering = $state(true);

	// Monotonic counter to prevent stale async renders.
	let renderGeneration = 0;

	async function render() {
		const gen = ++renderGeneration;
		rendering = true;

		try {
			// Configure marked with a custom code block renderer that uses Shiki.
			const codeBlocks: { lang: string; code: string; placeholder: string }[] = [];

			const renderer = new marked.Renderer();

			// Override code block rendering: insert a placeholder, then replace
			// with Shiki-highlighted HTML asynchronously.
			renderer.code = function ({ text, lang }: { text: string; lang?: string }) {
				const resolvedLang = lang ? resolveLanguage(lang) : null;
				if (resolvedLang) {
					const placeholder = `<!--shiki-${codeBlocks.length}-->`;
					codeBlocks.push({ lang: resolvedLang, code: text, placeholder });
					return placeholder;
				}
				// No Shiki support -- render as plain code block.
				const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
				return `<pre class="md-code-block"><code>${escaped}</code></pre>`;
			};

			const rawHtml = await marked.parse(content, { renderer, async: true });
			if (gen !== renderGeneration) return;

			let html = rawHtml;

			// Highlight code blocks with Shiki.
			if (codeBlocks.length > 0) {
				try {
					const hl = await getHighlighter();
					if (gen !== renderGeneration) return;

					const theme = themeStore.current === 'dark' ? 'vitesse-dark' : 'vitesse-light';

					for (const block of codeBlocks) {
						const lang = block.lang as BundledLanguage;
						const loaded = hl.getLoadedLanguages();
						if (!loaded.includes(lang)) {
							await hl.loadLanguage(lang);
						}
						if (gen !== renderGeneration) return;

						const highlighted = hl.codeToHtml(block.code, { lang, theme });
						html = html.replace(block.placeholder, highlighted);
					}
				} catch (e) {
					reportError(e, { silent: true, category: 'rendering' });
					for (const block of codeBlocks) {
						const escaped = block.code
							.replace(/&/g, '&amp;')
							.replace(/</g, '&lt;')
							.replace(/>/g, '&gt;');
						html = html.replace(
							block.placeholder,
							`<pre class="md-code-block"><code>${escaped}</code></pre>`,
						);
					}
				}
			}

			renderedHtml = sanitizeHtml(html);
		} catch (e) {
			reportError(e, { silent: true, category: 'rendering' });
			renderedHtml = `<pre>${content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>`;
		} finally {
			if (gen === renderGeneration) {
				rendering = false;
			}
		}
	}

	// Re-render when content or theme changes.
	$effect(() => {
		void content;
		void themeStore.current;
		render();
	});
</script>

<div class="md-viewer">
	{#if rendering}
		<div class="md-loading">
			<div class="md-loading-skeleton"></div>
		</div>
	{:else}
		<div class="md-content">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- Markdown rendered by marked + Shiki, then sanitized via sanitizeHtml before assignment -->
			{@html renderedHtml}
		</div>
	{/if}
</div>

<style>
	.md-viewer {
		flex: 1;
		overflow: auto;
		background: var(--bg);
	}

	/* Loading skeleton */
	.md-loading {
		padding: 24px;
	}

	.md-loading-skeleton {
		width: 80%;
		height: 120px;
		background: var(--bg-surface);
		border-radius: var(--radius);
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

	/* Markdown content styles */
	.md-content {
		padding: 24px 32px;
		max-width: 800px;
		line-height: 1.7;
		color: var(--text);
	}

	/* Headings */
	.md-content :global(h1) {
		font-size: 1.75em;
		font-weight: 700;
		margin: 1.5em 0 0.5em;
		padding-bottom: 0.3em;
		border-bottom: 1px solid var(--border);
		color: var(--text);
	}

	.md-content :global(h2) {
		font-size: 1.4em;
		font-weight: 600;
		margin: 1.25em 0 0.4em;
		padding-bottom: 0.25em;
		border-bottom: 1px solid var(--border);
		color: var(--text);
	}

	.md-content :global(h3) {
		font-size: 1.2em;
		font-weight: 600;
		margin: 1em 0 0.4em;
		color: var(--text);
	}

	.md-content :global(h4),
	.md-content :global(h5),
	.md-content :global(h6) {
		font-size: 1em;
		font-weight: 600;
		margin: 0.8em 0 0.3em;
		color: var(--text);
	}

	/* Paragraphs */
	.md-content :global(p) {
		margin: 0.6em 0;
	}

	/* Links */
	.md-content :global(a) {
		color: var(--accent);
		text-decoration: none;
	}

	.md-content :global(a:hover) {
		color: var(--accent-hover);
		text-decoration: underline;
	}

	/* Bold, italic, strikethrough */
	.md-content :global(strong) {
		font-weight: 600;
		color: var(--text);
	}

	.md-content :global(del) {
		color: var(--text-muted);
	}

	/* Inline code */
	.md-content :global(code) {
		font-family: var(--font-mono, monospace);
		font-size: 0.85em;
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: 3px;
		padding: 0.15em 0.4em;
	}

	/* Code blocks (plain fallback) */
	.md-content :global(pre.md-code-block) {
		background: var(--bg-surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 12px 16px;
		overflow-x: auto;
		font-family: var(--font-mono, monospace);
		font-size: 12px;
		line-height: 1.6;
		margin: 0.8em 0;
	}

	.md-content :global(pre.md-code-block code) {
		background: none;
		border: none;
		padding: 0;
		font-size: inherit;
	}

	/* Shiki code blocks */
	.md-content :global(pre.shiki) {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 12px 16px;
		overflow-x: auto;
		font-family: var(--font-mono, monospace);
		font-size: 12px;
		line-height: 1.6;
		margin: 0.8em 0;
	}

	.md-content :global(pre.shiki code) {
		background: none;
		border: none;
		padding: 0;
		font-size: inherit;
	}

	/* Lists */
	.md-content :global(ul),
	.md-content :global(ol) {
		margin: 0.5em 0;
		padding-left: 1.5em;
	}

	.md-content :global(li) {
		margin: 0.2em 0;
	}

	.md-content :global(li > ul),
	.md-content :global(li > ol) {
		margin: 0;
	}

	/* Blockquotes */
	.md-content :global(blockquote) {
		margin: 0.8em 0;
		padding: 0.5em 1em;
		border-left: 3px solid var(--accent);
		background: var(--bg-surface);
		color: var(--text-muted);
	}

	.md-content :global(blockquote p) {
		margin: 0.3em 0;
	}

	/* Tables */
	.md-content :global(table) {
		border-collapse: collapse;
		width: 100%;
		margin: 0.8em 0;
		font-size: 0.9em;
	}

	.md-content :global(th) {
		background: var(--bg-surface);
		font-weight: 600;
		text-align: left;
		padding: 6px 12px;
		border: 1px solid var(--border);
	}

	.md-content :global(td) {
		padding: 6px 12px;
		border: 1px solid var(--border);
	}

	.md-content :global(tr:nth-child(even)) {
		background: color-mix(in srgb, var(--bg-surface) 30%, var(--bg));
	}

	/* Horizontal rules */
	.md-content :global(hr) {
		border: none;
		border-top: 1px solid var(--border);
		margin: 1.5em 0;
	}

	/* Images */
	.md-content :global(img) {
		max-width: 100%;
		height: auto;
		border-radius: var(--radius);
	}
</style>
