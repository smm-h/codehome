<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAnchorAttributes } from 'svelte/elements';

	interface Props extends HTMLAnchorAttributes {
		children: Snippet;
	}

	let { children, class: className, ...rest }: Props = $props();
</script>

<a class="link {className ?? ''}" {...rest}>
	{@render children()}
</a>

<style>
	.link {
		color: var(--accent);
		text-decoration: none;
		transition: color 0.15s;
	}

	.link:hover {
		color: var(--accent-hover);
	}
</style>
