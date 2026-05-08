/**
 * URL helpers for branch routing.
 *
 * Branch names use a "repo:branch" qualified format internally and in API calls,
 * but the URL routing uses "/branch/repo/branch" with a slash separator.
 */

/** Build a branch URL from a qualified name (e.g. "bag:navchat-t" -> "/branch/bag/navchat-t"). */
export function branchUrl(qualified: string, tab?: string): string {
	const [repo, branch] = qualified.split(':', 2);
	return tab ? `/branch/${repo}/${branch}/${tab}` : `/branch/${repo}/${branch}`;
}

/** Reconstruct a qualified name from separate repo and branch params. */
export function qualifiedFromParams(repo: string, branch: string): string {
	return `${repo}:${branch}`;
}
