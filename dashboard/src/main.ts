/**
 * Application entry point.
 *
 * Mounts the root App component and initializes the client-side router.
 * Replaces SvelteKit's internal bootstrapping.
 */

import './app.css';
import { mount } from 'svelte';
import App from './App.svelte';
import { initRouter } from './lib/router/router.svelte.js';
import { routeTable } from './lib/router/routes.js';

// Initialize the router before mounting so the first render
// already has a matched route available.
initRouter(routeTable);

const target = document.getElementById('app');
if (!target) throw new Error('Missing #app mount point in index.html');
mount(App, { target });

// Clean up any previously registered service worker.
// The SW caused SSE disconnect issues and serves no purpose for a localhost dev tool.
if ('serviceWorker' in navigator) {
	navigator.serviceWorker.getRegistrations().then((regs) => {
		regs.forEach((r) => r.unregister());
	});
}
