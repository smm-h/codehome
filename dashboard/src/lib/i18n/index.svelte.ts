import { en } from './en.js';
import { it } from './it.js';

const STORAGE_KEY = 'supervisor-lang';
const DEFAULT_LANG =
	typeof navigator !== 'undefined' && navigator.language?.startsWith('it') ? 'it' : 'en';

function createI18nStore() {
	const stored = typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null;

	let lang = $state(stored || DEFAULT_LANG);

	function t(key: string, params: Record<string, string> = {}): string {
		const translations = lang === 'it' ? it : en;
		const str = translations[key] || en[key] || key;
		return Object.entries(params).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, v), str);
	}

	function setLang(newLang: string) {
		lang = newLang;
		if (typeof localStorage !== 'undefined') {
			localStorage.setItem(STORAGE_KEY, newLang);
		}
	}

	return {
		get lang() {
			return lang;
		},
		t,
		setLang,
	};
}

export const i18n = createI18nStore();
