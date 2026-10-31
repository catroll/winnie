import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { tr } from '../i18n';
import { PageHeader } from '../components/PageHeader';
import { formatDateTime } from '../lib/format';

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return tr('The request failed.');
}

export function AccountPage() {
  const { user, expiresAt, changePassword, logout } = useAuth();
  const navigate = useNavigate();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (newPassword.length < 8) {
      setError(tr('The new password must be at least 8 characters.'));
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(tr('The new passwords do not match.'));
      return;
    }
    if (newPassword === currentPassword) {
      setError(tr('The new password must differ from the current password.'));
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await changePassword(currentPassword, newPassword);
      // The session is gone; the route guard would redirect, but navigating
      // explicitly makes the intent clear. The AuthProvider carries the notice.
      navigate('/login', { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setError(tr('The current password is incorrect.'));
      } else {
        setError(messageOf(caught));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <>
      <PageHeader title="Account" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="row">
            <div className="col-lg-5">
              <div className="card mb-4">
                <div className="card-header">
                  <h3 className="card-title">{tr('Profile')}</h3>
                </div>
                <div className="card-body">
                  <dl className="row mb-0">
                    <dt className="col-5 text-secondary fw-normal">{tr('Username')}</dt>
                    <dd className="col-7 text-end font-monospace">{user?.username}</dd>

                    <dt className="col-5 text-secondary fw-normal">{tr('Display name')}</dt>
                    <dd className="col-7 text-end">{user?.display_name || '—'}</dd>

                    <dt className="col-5 text-secondary fw-normal">{tr('Member since')}</dt>
                    <dd className="col-7 text-end">{formatDateTime(user?.created_at ?? null)}</dd>

                    <dt className="col-5 text-secondary fw-normal">{tr('Session expires')}</dt>
                    <dd className="col-7 text-end">{formatDateTime(expiresAt)}</dd>

                    <dt className="col-5 text-secondary fw-normal">{tr('User ID')}</dt>
                    <dd className="col-7 text-end font-monospace text-break">{user?.id}</dd>
                  </dl>
                </div>
                <div className="card-footer">
                  <button type="button" className="btn btn-outline-secondary btn-sm" onClick={handleLogout}>
                    <i className="bi bi-box-arrow-right me-1" aria-hidden="true" />
                    {tr('Sign out')}
                  </button>
                </div>
              </div>
            </div>

            <div className="col-lg-7">
              <div className="card mb-4">
                <div className="card-header">
                  <h3 className="card-title">{tr('Change password')}</h3>
                </div>
                <form onSubmit={handleSubmit}>
                  <div className="card-body">
                    {error ? (
                      <div className="alert alert-danger py-2" role="alert">
                        {error}
                      </div>
                    ) : null}

                    <div className="mb-3">
                      <label className="form-label" htmlFor="current-password">
                        {tr('Current password')}
                      </label>
                      <input
                        id="current-password"
                        className="form-control"
                        type="password"
                        autoComplete="current-password"
                        value={currentPassword}
                        onChange={(event) => setCurrentPassword(event.target.value)}
                        disabled={submitting}
                        required
                      />
                    </div>

                    <div className="mb-3">
                      <label className="form-label" htmlFor="new-password">
                        {tr('New password')}
                      </label>
                      <input
                        id="new-password"
                        className="form-control"
                        type="password"
                        autoComplete="new-password"
                        minLength={8}
                        value={newPassword}
                        onChange={(event) => setNewPassword(event.target.value)}
                        disabled={submitting}
                        required
                      />
                      <div className="form-text">{tr('At least 8 characters.')}</div>
                    </div>

                    <div className="mb-0">
                      <label className="form-label" htmlFor="confirm-password">
                        {tr('Confirm new password')}
                      </label>
                      <input
                        id="confirm-password"
                        className="form-control"
                        type="password"
                        autoComplete="new-password"
                        minLength={8}
                        value={confirmPassword}
                        onChange={(event) => setConfirmPassword(event.target.value)}
                        disabled={submitting}
                        required
                      />
                    </div>
                  </div>
                  <div className="card-footer">
                    <div className="d-flex flex-wrap justify-content-between align-items-center gap-2">
                      <span className="small text-secondary">
                        {tr('Changing the password signs out every session, including this one.')}
                      </span>
                      <button type="submit" className="btn btn-primary" disabled={submitting}>
                        {submitting ? (
                          <>
                            <span
                              className="spinner-border spinner-border-sm me-1"
                              aria-hidden="true"
                            />
                            {tr('Updating…')}
                          </>
                        ) : (
                          tr('Change password')
                        )}
                      </button>
                    </div>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
