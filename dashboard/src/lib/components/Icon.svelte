<script lang="ts">
	import {
		Activity,
		AlertCircle,
		AlertTriangle,
		ArrowDown,
		ArrowLeft,
		Bell,
		BookOpen,
		Bot,
		Home,
		Check,
		CheckCircle,
		CheckSquare,
		ChevronDown,
		ChevronRight,
		ChevronUp,
		Circle,
		ClipboardCopy,
		Clock,
		Code,
		Columns2,
		Cpu,
		Database,
		Download,
		ExternalLink,
		Eye,
		EyeOff,
		File,
		Folder,
		FolderOpen,
		FolderTree,
		GitBranch,
		GitCompare,
		Globe,
		Inbox,
		LayoutDashboard,
		LayoutList,
		Link,
		Loader,
		Lock,
		Maximize,
		MessageCircle,
		MessageSquare,
		Minimize,
		Minus,
		Monitor,
		Moon,
		Package,
		Paintbrush,
		Palette,
		PanelLeftClose,
		PanelLeftOpen,
		Pencil,
		Play,
		Plug,
		Plus,
		Puzzle,
		RefreshCw,
		Rocket,
		RotateCw,
		Search,
		Server,
		Settings,
		Smartphone,
		Sparkles,
		Square,
		Sun,
		Tablet,
		Terminal,
		Ticket,
		Unlink,
		Upload,
		User,
		Users,
		X,
		XCircle,
	} from 'lucide-svelte';

	interface Props {
		name: string;
		size?: number;
	}

	let { name, size = 20 }: Props = $props();

	// Map icon name strings to Lucide components.
	// Keys must match the name="" values used throughout the dashboard.
	// Typed as Record<string, unknown> because lucide-svelte v1 exports Svelte 4
	// SvelteComponentTyped classes which don't satisfy Svelte 5's Component type.
	const iconMap: Record<string, unknown> = {
		activity: Activity,
		'alert-circle': AlertCircle,
		'alert-triangle': AlertTriangle,
		'arrow-down': ArrowDown,
		'arrow-left': ArrowLeft,
		bell: Bell,
		'book-open': BookOpen,
		bot: Bot,
		check: Check,
		'check-circle': CheckCircle,
		'check-square': CheckSquare,
		'chevron-down': ChevronDown,
		'chevron-right': ChevronRight,
		'chevron-up': ChevronUp,
		circle: Circle,
		'clipboard-copy': ClipboardCopy,
		clock: Clock,
		code: Code,
		'columns-2': Columns2,
		cpu: Cpu,
		database: Database,
		download: Download,
		'external-link': ExternalLink,
		eye: Eye,
		'eye-off': EyeOff,
		file: File,
		folder: Folder,
		'folder-open': FolderOpen,
		'folder-tree': FolderTree,
		'git-branch': GitBranch,
		'git-compare': GitCompare,
		globe: Globe,
		home: Home,
		inbox: Inbox,
		'layout-dashboard': LayoutDashboard,
		'layout-list': LayoutList,
		link: Link,
		loader: Loader,
		lock: Lock,
		maximize: Maximize,
		'message-circle': MessageCircle,
		'message-square': MessageSquare,
		minimize: Minimize,
		minus: Minus,
		monitor: Monitor,
		moon: Moon,
		package: Package,
		paintbrush: Paintbrush,
		palette: Palette,
		'panel-left-close': PanelLeftClose,
		'panel-left-open': PanelLeftOpen,
		pencil: Pencil,
		play: Play,
		plug: Plug,
		plus: Plus,
		puzzle: Puzzle,
		refresh: RefreshCw,
		rocket: Rocket,
		'rotate-cw': RotateCw,
		search: Search,
		server: Server,
		settings: Settings,
		smartphone: Smartphone,
		sparkles: Sparkles,
		square: Square,
		sun: Sun,
		tablet: Tablet,
		terminal: Terminal,
		ticket: Ticket,
		unlink: Unlink,
		upload: Upload,
		user: User,
		users: Users,
		x: X,
		'x-circle': XCircle,
	};

	const component = $derived(iconMap[name]);
</script>

{#if name === 'github'}
	<!-- GitHub brand mark (source: simpleicons.org/icons/github.svg) -->
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		overflow="visible"
		fill="currentColor"
		aria-hidden="true"
	>
		<path
			d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"
		/>
	</svg>
{:else if name === 'figma'}
	<!-- Figma brand mark (source: simpleicons.org/icons/figma.svg) -->
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		overflow="visible"
		fill="currentColor"
		aria-hidden="true"
	>
		<path
			d="M15.852 8.981h-4.588V0h4.588c2.476 0 4.49 2.014 4.49 4.49s-2.014 4.491-4.49 4.491zM12.735 7.51h3.117c1.665 0 3.019-1.355 3.019-3.019s-1.355-3.019-3.019-3.019h-3.117V7.51zm0 8.137V8.981h-4.588c-2.476 0-4.49 2.014-4.49 4.49s2.014 4.49 4.49 4.49h4.588v-2.334zm-4.588-.001c-1.665 0-3.019-1.355-3.019-3.019s1.355-3.019 3.019-3.019h3.117v6.038H8.147zm0 2.334c-2.476 0-4.49-2.014-4.49-4.49s2.014-4.49 4.49-4.49h4.588v8.98H8.147zM8.148 24c-2.476 0-4.49-2.014-4.49-4.49s2.014-4.49 4.49-4.49h4.588v4.49c0 2.476-2.014 4.49-4.588 4.49zm0-7.51c-1.665 0-3.019 1.355-3.019 3.019S6.482 22.53 8.148 22.53c1.665 0 3.117-1.355 3.117-3.019V16.49H8.148zm7.704-7.509c-2.476 0-4.49-2.014-4.49-4.49s2.014-4.49 4.49-4.49 4.49 2.014 4.49 4.49-2.014 4.49-4.49 4.49zm0-7.51c-1.665 0-3.019 1.355-3.019 3.02s1.355 3.019 3.019 3.019 3.019-1.355 3.019-3.019-1.354-3.02-3.019-3.02z"
		/>
	</svg>
{:else if name === 'git-logo'}
	<!-- Git brand mark (source: simpleicons.org/icons/git.svg) -->
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		overflow="visible"
		fill="currentColor"
		aria-hidden="true"
	>
		<path
			d="M23.546 10.93L13.067.452c-.604-.603-1.582-.603-2.188 0L8.708 2.627l2.76 2.76c.645-.215 1.379-.07 1.889.441.516.515.658 1.258.438 1.9l2.658 2.66c.645-.223 1.387-.078 1.9.435.721.72.721 1.884 0 2.604-.719.719-1.881.719-2.6 0-.539-.541-.674-1.337-.404-1.996L12.86 8.955v6.525c.176.086.342.203.488.348.713.721.713 1.883 0 2.6-.719.721-1.889.721-2.609 0-.719-.719-.719-1.879 0-2.598.182-.18.387-.316.605-.406V8.835c-.217-.091-.424-.222-.6-.401-.545-.545-.676-1.342-.396-2.009L7.636 3.7.45 10.881c-.6.605-.6 1.584 0 2.189l10.48 10.477c.604.604 1.582.604 2.186 0l10.43-10.43c.605-.603.605-1.582 0-2.187"
		/>
	</svg>
{:else if name === 'linear-logo'}
	<!-- Linear brand mark (source: simpleicons.org/icons/linear.svg) -->
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		overflow="visible"
		fill="currentColor"
		aria-hidden="true"
	>
		<path
			d="M2.886 4.18A11.982 11.982 0 0 1 11.99 0C18.624 0 24 5.376 24 12.009c0 3.64-1.62 6.903-4.18 9.105L2.887 4.18ZM1.817 5.626l16.556 16.556c-.524.33-1.075.62-1.65.866L.951 7.277c.247-.575.537-1.126.866-1.65ZM.322 9.163l14.515 14.515c-.71.172-1.443.282-2.195.322L0 11.358a12 12 0 0 1 .322-2.195Zm-.17 4.862 9.823 9.824a12.02 12.02 0 0 1-9.824-9.824Z"
		/>
	</svg>
{:else if name === 'notion'}
	<!-- Notion brand mark (source: simpleicons.org/icons/notion.svg) -->
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		overflow="visible"
		fill="currentColor"
		aria-hidden="true"
	>
		<path
			d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 1.968c-.42-.326-.981-.7-2.055-.607L3.01 2.295c-.466.046-.56.28-.374.466zm.793 3.08v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.841-.046.935-.56.935-1.167V6.354c0-.606-.233-.933-.748-.887l-15.177.887c-.56.047-.747.327-.747.933zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.608.327-1.168.514-1.635.514-.748 0-.935-.234-1.495-.933l-4.577-7.186v6.952L12.21 19s0 .84-1.168.84l-3.222.186c-.093-.186 0-.653.327-.746l.84-.233V9.854L7.822 9.76c-.094-.42.14-1.026.793-1.073l3.456-.233 4.764 7.279v-6.44l-1.215-.139c-.093-.514.28-.887.747-.933zM1.936 1.035l13.31-.98c1.634-.14 2.055-.047 3.082.7l4.249 2.986c.7.513.934.653.934 1.213v16.378c0 1.026-.373 1.634-1.68 1.726l-15.458.934c-.98.047-1.448-.093-1.962-.747l-3.129-4.06c-.56-.747-.793-1.306-.793-1.96V2.667c0-.839.374-1.54 1.447-1.632z"
		/>
	</svg>
{:else if name === 'slack'}
	<!-- Slack brand mark (source: simpleicons.org/icons/slack.svg) -->
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		overflow="visible"
		fill="currentColor"
		aria-hidden="true"
	>
		<path
			d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z"
		/>
	</svg>
{:else if component}
	{@const Comp = component as import('svelte').Component<any, any, any>}
	<Comp {size} />
{:else}
	<!-- Fallback: letter-in-circle for unmapped icon names -->
	<svg
		width={size}
		height={size}
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
		aria-hidden="true"
	>
		<rect x="3" y="3" width="18" height="18" rx="4" opacity="0.3" />
		<text
			x="12"
			y="16"
			text-anchor="middle"
			font-size="11"
			fill="currentColor"
			stroke="none"
			font-family="var(--font)"
		>
			{name.charAt(0).toUpperCase()}
		</text>
	</svg>
{/if}

<style>
	/* Lucide components render inline SVGs; keep consistent sizing/alignment */
	:global(.lucide) {
		display: inline-block;
		vertical-align: middle;
		flex-shrink: 0;
	}

	svg {
		display: inline-block;
		vertical-align: middle;
		flex-shrink: 0;
	}
</style>
