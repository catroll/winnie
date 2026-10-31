import type { User } from '../api/types';

const STORAGE_KEY = 'winnie.admin.session';

export interface StoredSession {
  token: string;
  expiresAt: string;
  user: User;
  /** True when the session was written to localStorage ("Remember me"). */
  persistent: boolean;
}

function parse(raw: string | null, persistent: boolean): StoredSession | null {
  if (!raw) {
    return null;
  }
  try {
    const value = JSON.parse(raw) as Partial<StoredSession>;
    if (
      typeof value.token !== 'string' ||
      typeof value.expiresAt !== 'string' ||
      !value.user ||
      typeof value.user.username !== 'string'
    ) {
      return null;
    }
    return {
      token: value.token,
      expiresAt: value.expiresAt,
      user: value.user,
      persistent,
    };
  } catch {
    return null;
  }
}

/**
 * Read the active session. sessionStorage wins over localStorage so a tab can
 * hold a non-remembered session while an older remembered one is still present.
 */
export function readSession(): StoredSession | null {
  try {
    const session = parse(sessionStorage.getItem(STORAGE_KEY), false);
    if (session) {
      return session;
    }
  } catch {
    // Storage can be unavailable; fall through to the persistent slot.
  }
  try {
    return parse(localStorage.getItem(STORAGE_KEY), true);
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  return readSession()?.token ?? null;
}

export function writeSession(session: Omit<StoredSession, 'persistent'> & { persistent: boolean }): void {
  clearSession();
  try {
    const storage = session.persistent ? localStorage : sessionStorage;
    storage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // A failed write leaves the user signed out on the next load; the login
    // response itself is still usable for this tab.
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore unavailable storage.
  }
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore unavailable storage.
  }
}

export function isExpired(session: StoredSession): boolean {
  const expiresAt = Date.parse(session.expiresAt);
  return Number.isFinite(expiresAt) && expiresAt <= Date.now();
}
