import type { ThemeTokens, ColorSet } from './types';

export function applyTheme(tokens: ThemeTokens, colorScheme: 'dark' | 'light'): void {
	const colors: ColorSet = tokens.colors[colorScheme];
	const el = document.documentElement;
	const s = el.style;

	s.setProperty('--bg', colors.bg);
	s.setProperty('--bg-surface', colors.bgSurface);
	s.setProperty('--bg-hover', colors.bgHover);
	s.setProperty('--text', colors.text);
	s.setProperty('--text-muted', colors.textMuted);
	s.setProperty('--text-dim', colors.textDim);
	s.setProperty('--border', colors.border);
	s.setProperty('--accent', colors.accent);
	s.setProperty('--accent-hover', colors.accentHover);
	s.setProperty('--accent-muted', colors.accentMuted);
	s.setProperty('--accent-text', colors.accentText);
	s.setProperty('--success', colors.success);
	s.setProperty('--warning', colors.warning);
	s.setProperty('--danger', colors.danger);

	s.setProperty('--error', colors.danger);
	s.setProperty('--primary', colors.accent);
	s.setProperty('--primary-hover', colors.accentHover);
	s.setProperty('--text-primary', colors.text);
	s.setProperty('--text-secondary', colors.textMuted);
	s.setProperty('--text-error', colors.danger);

	s.setProperty('--space-xs', `${tokens.spacing.xs}px`);
	s.setProperty('--space-sm', `${tokens.spacing.sm}px`);
	s.setProperty('--space-md', `${tokens.spacing.md}px`);
	s.setProperty('--space-lg', `${tokens.spacing.lg}px`);
	s.setProperty('--space-xl', `${tokens.spacing.xl}px`);
	s.setProperty('--space-xxl', `${tokens.spacing.xxl}px`);

	s.setProperty('--radius-sm', `${tokens.radii.sm}px`);
	s.setProperty('--radius', `${tokens.radii.md}px`);
	s.setProperty('--radius-lg', `${tokens.radii.lg}px`);
	s.setProperty('--radius-full', `${tokens.radii.full}px`);

	s.setProperty('--font', tokens.typography.fontFamily);
	s.setProperty('--font-mono', tokens.typography.fontFamilyMono);
	s.setProperty('--font-size-caption', `${tokens.typography.sizeCaption}px`);
	s.setProperty('--font-size-body', `${tokens.typography.sizeBody}px`);
	s.setProperty('--font-size-heading', `${tokens.typography.sizeHeading}px`);
	s.setProperty('--font-size-title', `${tokens.typography.sizeTitle}px`);
	s.setProperty('--font-weight-normal', `${tokens.typography.weightNormal}`);
	s.setProperty('--font-weight-medium', `${tokens.typography.weightMedium}`);
	s.setProperty('--font-weight-bold', `${tokens.typography.weightBold}`);
	s.setProperty('--line-height', `${tokens.typography.lineHeight}`);

	s.setProperty('--shadow-sm', tokens.shadows.sm);
	s.setProperty('--shadow-md', tokens.shadows.md);

	el.dataset.theme = colorScheme;
}
