import { minimalTheme } from './minimal';
import { denseTheme } from './dense';
import { familiarTheme } from './familiar';
import type { ThemeTokens } from './types';

export const themes: Record<string, ThemeTokens> = {
	minimal: minimalTheme,
	dense: denseTheme,
	familiar: familiarTheme,
};

export { applyTheme } from './apply';
export type { ThemeTokens } from './types';
