import { describe, it, expect } from 'vitest';
import { branchUrl, qualifiedFromParams } from './url';

describe('branchUrl', () => {
	it('converts a qualified name to a URL path', () => {
		expect(branchUrl('bag:navchat-t')).toBe('/branch/bag/navchat-t');
	});

	it('appends a tab segment when provided', () => {
		expect(branchUrl('bag:navchat-t', 'chat')).toBe('/branch/bag/navchat-t/chat');
	});

	it('handles branch names with hyphens and numbers', () => {
		expect(branchUrl('infra:fix-auth-42')).toBe('/branch/infra/fix-auth-42');
	});

	it('uses only the first colon as separator (split limit behavior)', () => {
		// JS split(':', 2) returns ['bag', 'some'] -- the rest is discarded.
		// Branch names should not contain colons in practice.
		expect(branchUrl('bag:some:weird:name')).toBe('/branch/bag/some');
	});

	it('returns a path without trailing slash when no tab is given', () => {
		const url = branchUrl('chat:feature');
		expect(url.endsWith('/')).toBe(false);
	});
});

describe('qualifiedFromParams', () => {
	it('joins repo and branch with a colon', () => {
		expect(qualifiedFromParams('bag', 'navchat-t')).toBe('bag:navchat-t');
	});

	it('preserves special characters in branch name', () => {
		expect(qualifiedFromParams('infra', 'fix/slash')).toBe('infra:fix/slash');
	});

	it('round-trips with branchUrl', () => {
		const qualified = qualifiedFromParams('bag', 'my-branch');
		const url = branchUrl(qualified);
		expect(url).toBe('/branch/bag/my-branch');
	});
});
