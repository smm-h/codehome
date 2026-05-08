<script lang="ts">
	import type { Snippet } from 'svelte';
	import { goto } from './router.svelte.js';

	/**
	 * Client-side navigation link. Renders a standard <a> tag but intercepts
	 * clicks to use the router's goto() instead of a full page navigation.
	 *
	 * Passes through all extra attributes (class, title, aria-*, etc.).
	 */
	interface Props {
		href: string;
		replace?: boolean;
		children: Snippet;
		[key: string]: unknown;
	}

	let { href, replace = false, children, ...rest }: Props = $props();

	function handleClick(e: MouseEvent) {
		// Allow modifier-key clicks to open in new tab as expected.
		if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
		// Only handle left clicks.
		if (e.button !== 0) return;

		e.preventDefault();
		goto(href, { replaceState: replace });
	}
</script>

<a {href} onclick={handleClick} {...rest}>
	{@render children()}
</a>
