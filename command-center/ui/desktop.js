/* The desktop launches registered pages only. No parallel router or status data. */
export function preferredTheme(saved) {
  return saved === 'dark' ? 'dark' : 'light';
}

export function desktopPages(pages, landing) {
  const byId = new Map(pages.map(page => [page.id, page]));
  return [...new Set([landing, 'mission_console', 'apps', 'video-studio', 'web_designer', 'control'])]
    .filter(id => byId.has(id))
    .map(id => byId.get(id));
}
