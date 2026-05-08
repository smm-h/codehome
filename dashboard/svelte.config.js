import { sep } from 'node:path';

/** @type {import('@sveltejs/vite-plugin-svelte').SvelteConfig} */
const config = {
	compilerOptions: {
		// Enable runes for project code but not for external libraries
		// (e.g. lucide-svelte uses $$props which is invalid in runes mode).
		runes: ({ filename }) => {
			if (!filename) return true;
			const segments = filename.toLowerCase().split(sep);
			return segments.includes('node_modules') ? undefined : true;
		},
	},
};

export default config;
