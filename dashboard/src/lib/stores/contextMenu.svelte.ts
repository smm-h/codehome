export interface MenuItem {
	label: string;
	icon?: string;
	action: () => void;
	danger?: boolean;
	disabled?: boolean;
	separator?: boolean;
}

interface MenuState {
	open: boolean;
	x: number;
	y: number;
	items: MenuItem[];
}

function createContextMenuStore() {
	let state = $state<MenuState>({ open: false, x: 0, y: 0, items: [] });

	function showMenu(x: number, y: number, items: MenuItem[]) {
		state = { open: true, x, y, items };
	}

	function hideMenu() {
		state = { open: false, x: 0, y: 0, items: [] };
	}

	return {
		get open() {
			return state.open;
		},
		get x() {
			return state.x;
		},
		get y() {
			return state.y;
		},
		get items() {
			return state.items;
		},
		showMenu,
		hideMenu,
	};
}

export const contextMenu = createContextMenuStore();

/**
 * Svelte action that attaches a contextmenu listener to a DOM node.
 * Usage: <tr use:contextItems={() => [{ label: 'Open', action: () => ... }]}>
 */
export function contextItems(node: HTMLElement, getItems: () => MenuItem[]) {
	function onContextMenu(e: MouseEvent) {
		const items = getItems();
		// Don't open an empty menu -- fall through to the global handler instead.
		if (items.length === 0) return;
		e.preventDefault();
		e.stopPropagation();
		contextMenu.showMenu(e.clientX, e.clientY, items);
	}

	node.addEventListener('contextmenu', onContextMenu);

	return {
		destroy() {
			node.removeEventListener('contextmenu', onContextMenu);
		},
	};
}
