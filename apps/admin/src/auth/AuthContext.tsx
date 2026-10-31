import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  ApiError,
  api,
  setForbiddenHandler,
  setUnauthorizedHandler,
} from '../api/client';
import type { User } from '../api/types';
import {
  clearSession,
  isExpired,
  readSession,
  writeSession,
  type StoredSession,
} from './storage';

interface AuthContextValue {
  user: User | null;
  /** True when the current account may use operator-only routes. */
  isAdmin: boolean;
  expiresAt: string | null;
  /** True while the stored session is being validated against the API. */
  checking: boolean;
  /** A one-off message for the login page, e.g. after a password change. */
  flash: string | null;
  login: (username: string, password: string, remember: boolean) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthState {
  session: StoredSession | null;
  checking: boolean;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(() => {
    const session = readSession();
    return { session, checking: session !== null };
  });
  const [flash, setFlash] = useState<string | null>(null);

  // A 401 on any authenticated request means the session is gone. Drop it and
  // let the route guards send the user to the login page. A 403 does not mean
  // the session is invalid: re-read /me so a demotion takes effect in the UI
  // as well as in the API.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearSession();
      setState({ session: null, checking: false });
    });
    setForbiddenHandler(() => {
      void api
        .me()
        .then((user) => {
          setState((current) => {
            if (!current.session) return current;
            const refreshed: StoredSession = { ...current.session, user };
            writeSession(refreshed);
            return { session: refreshed, checking: false };
          });
        })
        .catch(() => {
          // Keep the current session; a failed refresh must not sign the user
          // out. The next request still gets 403 from the server.
        });
    });
    return () => {
      setUnauthorizedHandler(null);
      setForbiddenHandler(null);
    };
  }, []);

  // Validate the stored token once at startup. A network failure keeps the
  // cached session (the token may still be valid); only a 401 signs out.
  useEffect(() => {
    const stored = readSession();
    if (!stored) {
      setState({ session: null, checking: false });
      return;
    }
    if (isExpired(stored)) {
      clearSession();
      setState({ session: null, checking: false });
      return;
    }

    setState({ session: stored, checking: true });
    let alive = true;
    api
      .me()
      .then((user) => {
        if (!alive) {
          return;
        }
        const refreshed: StoredSession = { ...stored, user };
        writeSession(refreshed);
        setState({ session: refreshed, checking: false });
      })
      .catch((error: unknown) => {
        if (!alive) {
          return;
        }
        if (error instanceof ApiError && error.status === 401) {
          clearSession();
          setState({ session: null, checking: false });
          return;
        }
        setState({ session: stored, checking: false });
      });

    return () => {
      alive = false;
    };
  }, []);

  const login = useCallback(
    async (username: string, password: string, remember: boolean) => {
      const response = await api.login(username, password);
      const session: StoredSession = {
        token: response.access_token,
        expiresAt: response.expires_at,
        user: response.user,
        persistent: remember,
      };
      writeSession(session);
      setFlash(null);
      setState({ session, checking: false });
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // The local session is cleared even when the server cannot be reached.
    }
    clearSession();
    setFlash(null);
    setState({ session: null, checking: false });
  }, []);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      // The API revokes every session, including this one, on success.
      await api.changePassword(currentPassword, newPassword);
      clearSession();
      setFlash(
        'Password changed. Every session was signed out; sign in with your new password.',
      );
      setState({ session: null, checking: false });
    },
    [],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      user: state.session?.user ?? null,
      isAdmin: state.session?.user.role === 'admin',
      expiresAt: state.session?.expiresAt ?? null,
      checking: state.checking,
      flash,
      login,
      logout,
      changePassword,
    }),
    [state, flash, login, logout, changePassword],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return context;
}
