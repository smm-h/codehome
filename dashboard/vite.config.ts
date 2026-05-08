import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';
import { resolve, sep } from 'node:path';

export default defineConfig({
	plugins: [
		svelte({
			compilerOptions: {
				// Enable runes for project code but not for external libraries
				// (e.g. lucide-svelte uses $$props which is invalid in runes mode).
				runes: ({ filename }) => {
					if (!filename) return true;
					const segments = filename.toLowerCase().split(sep);
					if (!segments.includes('node_modules')) return true;
					return undefined;
				},
			},
		}),
	],
	resolve: {
		alias: {
			$lib: resolve(__dirname, 'src/lib'),
		},
	},
	server: {
		// When spawned by the codehome server, it handles /api and /events.
		// When running standalone (`npm run dev`), proxy API calls.
		proxy: process.env.VITE_EMBEDDED
			? undefined
			: {
					'/api': 'http://127.0.0.1:9100',
					'/events': 'http://127.0.0.1:9100',
				},
	},
	build: {
		outDir: 'dist',
		emptyOutDir: true,
	},
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		globals: true,
	},
});
