export interface DataGridColumn<T = any> {
	key: string;
	label: string;
	sortable?: boolean;
	filterable?: boolean;
	width?: number;
	minWidth?: number;
	hidden?: boolean;
	align?: 'left' | 'center' | 'right';
}

export interface DataGridState {
	sortKey: string | null;
	sortDir: 'asc' | 'desc';
	filters: Record<string, string>;
	visibleColumns: string[];
	columnOrder: string[];
	page: number;
	pageSize: number;
}

export interface DataGridProps<T = any> {
	gridId: string;
	columns: DataGridColumn<T>[];
	rows: T[];
	totalRows?: number;
	pageSize?: number;
	sortable?: boolean;
	filterable?: boolean;
	selectable?: boolean;
}
