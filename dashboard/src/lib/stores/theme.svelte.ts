import { themes, applyTheme } from '$lib/themes';

const STORAGE_KEY = 'codehome-theme';
const THEME_ID_KEY = 'codehome-theme-id';
const PREFERENCE_KEY = 'codehome-theme-preference';

type ThemeId = 'minimal' | 'dense' | 'familiar';
type ColorSchemePreference = 'auto' | 'dark' | 'light';

function resolveColorScheme(pref: ColorSchemePreference): 'dark' | 'light' {
	if (pref !== 'auto') return pref;
	if (typeof window === 'undefined') return 'dark';
	return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function createThemeStore() {
	const storedId =
		typeof localStorage !== 'undefined'
			? (localStorage.getItem(THEME_ID_KEY) as ThemeId | null)
			: null;
	const storedPref =
		typeof localStorage !== 'undefined'
			? (localStorage.getItem(PREFERENCE_KEY) as ColorSchemePreference | null)
			: null;
	const legacyTheme =
		typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null;

	const initialPref: ColorSchemePreference =
		storedPref ?? (legacyTheme === 'dark' || legacyTheme === 'light' ? legacyTheme : 'auto');
	const initialId: ThemeId = storedId && storedId in themes ? storedId : 'familiar';

	let themeId = $state<ThemeId>(initialId);
	let colorScheme = $state<ColorSchemePreference>(initialPref);
	let resolved = $state<'dark' | 'light'>(resolveColorScheme(initialPref));

	function apply() {
		resolved = resolveColorScheme(colorScheme);
		if (typeof document !== 'undefined') {
			applyTheme(themes[themeId], resolved);
			localStorage.setItem(THEME_ID_KEY, themeId);
			localStorage.setItem(PREFERENCE_KEY, colorScheme);
			localStorage.setItem(STORAGE_KEY, resolved);
		}
	}

	apply();

	function setTheme(id: ThemeId) {
		themeId = id;
		apply();
	}

	function setColorScheme(scheme: ColorSchemePreference) {
		colorScheme = scheme;
		apply();
	}

	function toggle() {
		colorScheme = resolved === 'dark' ? 'light' : 'dark';
		apply();
	}

	if (typeof window !== 'undefined') {
		window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
			if (colorScheme === 'auto') {
				apply();
			}
		});
	}

	return {
		get themeId() {
			return themeId;
		},
		get colorScheme() {
			return colorScheme;
		},
		get current() {
			return resolved;
		},
		setTheme,
		setColorScheme,
		toggle,
	};
}

export const themeStore = createThemeStore();
