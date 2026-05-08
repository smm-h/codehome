export interface NavItem {
	key: string;
	label: string;
	icon: string;
	path: string;
	source: 'core' | 'plugin';
	visible: boolean;
}

export interface NavLayoutProps {
	items: NavItem[];
	activeItem: string;
	onNavigate: (path: string) => void;
}
