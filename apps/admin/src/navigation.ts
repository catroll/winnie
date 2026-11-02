/** One source of truth for navigation, grouping and route access. */

export type NavigationGroup = 'workspace' | 'administration';

export interface NavigationItem {
  to: string;
  label: string;
  icon: string;
  end: boolean;
  group: NavigationGroup;
  adminOnly: boolean;
}

export const NAVIGATION_GROUPS: ReadonlyArray<{
  id: NavigationGroup;
  label: string;
}> = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'administration', label: 'Administration' },
];

export const NAVIGATION: readonly NavigationItem[] = [
  { to: '/inbox', label: 'Inbox', icon: 'bi-inbox', end: false, group: 'workspace', adminOnly: false },
  { to: '/mail', label: 'Mail', icon: 'bi-envelope', end: false, group: 'workspace', adminOnly: false },
  { to: '/chat', label: 'Chat', icon: 'bi-chat-dots', end: false, group: 'workspace', adminOnly: false },
  { to: '/meetings', label: 'Meetings', icon: 'bi-camera-video', end: false, group: 'workspace', adminOnly: false },
  { to: '/phone', label: 'Phone data', icon: 'bi-phone', end: false, group: 'workspace', adminOnly: false },
  { to: '/tasks', label: 'Scheduled tasks', icon: 'bi-alarm', end: false, group: 'workspace', adminOnly: false },
  { to: '/skills', label: 'Skills', icon: 'bi-puzzle', end: false, group: 'workspace', adminOnly: false },
  { to: '/keys', label: 'API keys', icon: 'bi-key', end: false, group: 'workspace', adminOnly: false },
  { to: '/account', label: 'Account', icon: 'bi-person-gear', end: false, group: 'workspace', adminOnly: false },
  { to: '/settings', label: 'Settings', icon: 'bi-gear', end: false, group: 'workspace', adminOnly: false },
  { to: '/', label: 'Dashboard', icon: 'bi-speedometer2', end: true, group: 'administration', adminOnly: true },
  { to: '/schedule', label: 'Schedule', icon: 'bi-calendar-week', end: false, group: 'administration', adminOnly: true },
  { to: '/users', label: 'Users', icon: 'bi-people', end: false, group: 'administration', adminOnly: true },
  { to: '/ai', label: 'AI providers', icon: 'bi-cpu', end: false, group: 'administration', adminOnly: true },
  { to: '/gateway', label: 'Gateway routes', icon: 'bi-signpost-split', end: false, group: 'administration', adminOnly: true },
  { to: '/index', label: 'Document index', icon: 'bi-journal-text', end: false, group: 'administration', adminOnly: true },
  { to: '/errors', label: 'Errors', icon: 'bi-bug', end: false, group: 'administration', adminOnly: true },
  { to: '/news', label: 'News', icon: 'bi-newspaper', end: false, group: 'administration', adminOnly: true },
];

export function visibleNavigation(isAdmin: boolean): NavigationItem[] {
  return NAVIGATION.filter((item) => isAdmin || !item.adminOnly);
}
