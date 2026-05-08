export interface ColorSet {
	bg: string;
	bgSurface: string;
	bgHover: string;
	text: string;
	textMuted: string;
	textDim: string;
	border: string;
	accent: string;
	accentHover: string;
	accentMuted: string;
	accentText: string;
	success: string;
	warning: string;
	danger: string;
}

export interface SpacingScale {
	xs: number;
	sm: number;
	md: number;
	lg: number;
	xl: number;
	xxl: number;
}

export interface RadiiScale {
	sm: number;
	md: number;
	lg: number;
	full: number;
}

export interface TypographyScale {
	fontFamily: string;
	fontFamilyMono: string;
	sizeCaption: number;
	sizeBody: number;
	sizeHeading: number;
	sizeTitle: number;
	weightNormal: number;
	weightMedium: number;
	weightBold: number;
	lineHeight: number;
}

export interface ShadowScale {
	none: string;
	sm: string;
	md: string;
}

export interface ThemeTokens {
	id: string;
	name: string;
	colors: { dark: ColorSet; light: ColorSet };
	spacing: SpacingScale;
	radii: RadiiScale;
	typography: TypographyScale;
	shadows: ShadowScale;
}
