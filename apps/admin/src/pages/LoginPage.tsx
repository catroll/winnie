import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { tr } from '../i18n';
import { LanguageSelect } from '../i18n/LanguageSwitcher';
import { useApi, useTitle } from '../lib/hooks';

interface LoginLocationState {
  from?: { pathname?: string; search?: string };
}

export function LoginPage() {
  useTitle('Sign in');
  const { login, flash } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state as LoginLocationState | null) ?? null;

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const health = useApi(() => api.health(), []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError(tr('Enter your username and password.'));
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await login(username.trim(), password, remember);
      const target = state?.from?.pathname
        ? `${state.from.pathname}${state.from.search ?? ''}`
        : '/';
      navigate(target, { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.status === 401) {
          setError(tr('Invalid username or password.'));
        } else if (caught.status === 429) {
          setError(
            caught.retryAfterSeconds
              ? tr(
                  'Too many failed sign-in attempts. Try again in {{seconds}} seconds.',
                  { seconds: caught.retryAfterSeconds },
                )
              : tr('Too many failed sign-in attempts. Try again later.'),
          );
        } else {
          setError(caught.detail);
        }
      } else {
        setError(tr('Sign in failed. Check that the API is running and try again.'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page bg-body-secondary">
      <div className="position-fixed top-0 end-0 p-3" style={{ zIndex: 1080 }}>
        <LanguageSelect className="w-auto" />
      </div>
      <h1 className="login-logo">
        <Link to="/login">
          <b>Winnie</b> Admin
        </Link>
      </h1>

      <div className="login-box">
        <div className="card">
          <div className="card-body login-card-body">
            <p className="login-box-msg">{tr('Sign in to manage your Winnie instance')}</p>

            {flash ? (
              <div className="alert alert-success py-2" role="status">
                {tr(flash)}
              </div>
            ) : null}
            {error ? (
              <div className="alert alert-danger py-2" role="alert">
                {error}
              </div>
            ) : null}

            <form onSubmit={handleSubmit} noValidate>
              <label className="visually-hidden" htmlFor="login-username">
                {tr('Username')}
              </label>
              <div className="input-group mb-3">
                <input
                  id="login-username"
                  name="username"
                  type="text"
                  className="form-control"
                  placeholder={tr('Username')}
                  autoComplete="username"
                  autoFocus
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  disabled={submitting}
                  required
                />
                <div className="input-group-text">
                  <span className="bi bi-person" aria-hidden="true" />
                </div>
              </div>

              <label className="visually-hidden" htmlFor="login-password">
                {tr('Password')}
              </label>
              <div className="input-group mb-3">
                <input
                  id="login-password"
                  name="password"
                  type="password"
                  className="form-control"
                  placeholder={tr('Password')}
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={submitting}
                  required
                />
                <div className="input-group-text">
                  <span className="bi bi-lock-fill" aria-hidden="true" />
                </div>
              </div>

              <div className="row">
                <div className="col-7">
                  <div className="form-check">
                    <input
                      className="form-check-input"
                      type="checkbox"
                      id="login-remember"
                      checked={remember}
                      onChange={(event) => setRemember(event.target.checked)}
                      disabled={submitting}
                    />
                    <label className="form-check-label" htmlFor="login-remember">
                      {tr('Remember me')}
                    </label>
                  </div>
                </div>
                <div className="col-5">
                  <div className="d-grid">
                    <button
                      type="submit"
                      className="btn btn-primary"
                      disabled={submitting}
                    >
                      {submitting ? (
                        <>
                          <span
                            className="spinner-border spinner-border-sm me-1"
                            aria-hidden="true"
                          />
                          {tr('Signing in…')}
                        </>
                      ) : (
                        tr('Sign in')
                      )}
                    </button>
                  </div>
                </div>
              </div>
            </form>
          </div>
        </div>
      </div>

      <p className="small text-secondary mt-3 mb-0" role="status">
        {health.status === 'success' && health.data ? (
          <>
            <i className="bi bi-check-circle-fill text-success me-1" aria-hidden="true" />
            {tr('API {{version}} online', { version: health.data.version })}
          </>
        ) : health.status === 'error' ? (
          <>
            <i className="bi bi-exclamation-circle-fill text-danger me-1" aria-hidden="true" />
            {tr('API unreachable')}
          </>
        ) : (
          tr('Checking API…')
        )}
      </p>
    </div>
  );
}
