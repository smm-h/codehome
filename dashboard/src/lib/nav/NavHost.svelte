<script lang="ts">
	import { preferences } from '$lib/stores/preferences.svelte.js';
	import NavSecondRow from './NavSecondRow.svelte';
	import NavDropdown from './NavDropdown.svelte';
	import NavInlineSwap from './NavInlineSwap.svelte';
	import NavFlatTabs from './NavFlatTabs.svelte';
	import type { NavLayoutProps } from './types';

	let { items, activeItem, onNavigate }: NavLayoutProps = $props();

	const layoutMap: Record<string, typeof NavSecondRow> = {
		'second-row': NavSecondRow,
		'dropdown': NavDropdown,
		'inline-swap': NavInlineSwap,
		'flat-tabs': NavFlatTabs,
	};

	const ActiveLayout = $derived(layoutMap[preferences.navLayout] || NavSecondRow);
</script>

<ActiveLayout {items} {activeItem} {onNavigate} />
