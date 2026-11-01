import { useState, type FormEvent } from 'react';

import { ApiError, api } from '../api/client';
import type { GatewayKey } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return tr('The request failed.');
}

/** A caller's own gateway keys; an administrator may switch to every account's. */
export function KeysPage() {
  const { isAdmin } = useAuth();
  const [showAll, setShowAll] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [selectedRoutes, setSelectedRoutes] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);

  const scope = showAll && isAdmin ? 'all' : 'own';
  const keys = useApi(() => api.gatewayKeys(scope), [scope]);

  const availableRoutes = keys.data?.routes ?? [];
  const activeKeys = (keys.data?.keys ?? []).filter((key) => !key.revoked_at);

  const toggleRoute = (name: string) => {
    setSelectedRoutes((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name],
    );
  };

  const createKey = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setActionError(null);
    setBusy('create');
    try {
      const created = await api.createGatewayKey({
        label: label.trim(),
        routes: selectedRoutes,
      });
      setSecret(created.secret);
      setCreateOpen(false);
      setLabel('');
      setSelectedRoutes([]);
      keys.reload();
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setBusy(null);
    }
  };

  const revokeKey = async (target: GatewayKey) => {
    if (
      !window.confirm(
        tr(
          'Revoke the key "{{label}}"? Every client using it stops working immediately.',
          { label: target.label },
        ),
      )
    ) {
      return;
    }
    setActionError(null);
    setBusy(`revoke-${target.id}`);
    try {
      await api.revokeGatewayKey(target.id);
      keys.reload();
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader title="API keys" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="alert alert-info" role="note">
            <i className="bi bi-info-circle-fill me-2" aria-hidden="true" />
            {tr(
              'A gateway key authenticates one of your clients. It is shown once and only a digest is stored. Clients send it as Authorization: Bearer, x-api-key, or x-winnie-key.',
            )}
          </div>

          {secret ? (
            <div className="alert alert-success" role="alert">
              <div className="d-flex justify-content-between align-items-start gap-3">
                <div className="flex-grow-1">
                  <p className="mb-2">
                    {tr(
                      'This key is shown once and cannot be recovered. Store it where the client reads its credentials.',
                    )}
                  </p>
                  <div className="input-group">
                    <input
                      className="form-control font-monospace"
                      readOnly
                      value={secret}
                      aria-label={tr('Gateway key')}
                    />
                    <button
                      type="button"
                      className="btn btn-outline-secondary"
                      onClick={() => {
                        void navigator.clipboard?.writeText(secret);
                      }}
                    >
                      {tr('Copy')}
                    </button>
                  </div>
                </div>
                <button
                  type="button"
                  className="btn-close"
                  aria-label={tr('I saved the key')}
                  onClick={() => setSecret(null)}
                />
              </div>
            </div>
          ) : null}

          {actionError ? (
            <div className="alert alert-danger py-2" role="alert">
              {actionError}
            </div>
          ) : null}

          <div className="card">
            <div className="card-header">
              <h3 className="card-title mb-0">{tr('Gateway keys')}</h3>
              <div className="card-tools d-flex align-items-center gap-3">
                {isAdmin ? (
                  <div className="form-check mb-0">
                    <input
                      type="checkbox"
                      className="form-check-input"
                      id="keys-show-all"
                      checked={showAll}
                      onChange={(event) => setShowAll(event.target.checked)}
                    />
                    <label className="form-check-label small" htmlFor="keys-show-all">
                      {tr("All users' keys")}
                    </label>
                  </div>
                ) : null}
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={!keys.data}
                  onClick={() => {
                    setActionError(null);
                    setCreateOpen((open) => !open);
                  }}
                >
                  <i className="bi bi-key me-1" aria-hidden="true" />
                  {tr('Create key')}
                </button>
              </div>
            </div>
            <div className="card-body">
              {keys.status === 'loading' ? (
                <LoadingBlock label="Loading keys…" />
              ) : keys.status === 'error' ? (
                <ErrorBlock error={keys.error} onRetry={keys.reload} />
              ) : (
                <>
                  {activeKeys.length === 0 ? (
                    <div className="alert alert-warning" role="alert">
                      {showAll
                        ? tr('No active gateway key exists.')
                        : tr(
                            'You have no active gateway key. Clients using a route answer 401 until you create one.',
                          )}
                    </div>
                  ) : null}

                  {createOpen ? (
                    <form className="border rounded p-3 mb-3" onSubmit={createKey}>
                      <div className="row g-3">
                        <div className="col-md-5">
                          <label className="form-label" htmlFor="gateway-key-label">
                            {tr('Label')}
                          </label>
                          <input
                            id="gateway-key-label"
                            className="form-control"
                            value={label}
                            onChange={(event) => setLabel(event.target.value)}
                            maxLength={64}
                            placeholder={tr('Laptop, phone, build agent…')}
                            required
                          />
                          <div className="form-text">
                            {tr('Names the client this key belongs to.')}
                          </div>
                        </div>
                        <div className="col-md-7">
                          <span className="form-label d-block">{tr('Allowed routes')}</span>
                          {availableRoutes.length === 0 ? (
                            <div className="text-secondary small">
                              {tr('No routes are configured yet.')}
                            </div>
                          ) : (
                            <div className="d-flex flex-wrap gap-3">
                              {availableRoutes.map((name) => (
                                <label className="form-check mb-0" key={name}>
                                  <input
                                    type="checkbox"
                                    className="form-check-input"
                                    checked={selectedRoutes.includes(name)}
                                    onChange={() => toggleRoute(name)}
                                  />
                                  <span className="form-check-label font-monospace small">
                                    {name}
                                  </span>
                                </label>
                              ))}
                            </div>
                          )}
                          <div className="form-text">
                            {tr('Leave every box unchecked to allow all routes.')}
                          </div>
                        </div>
                      </div>
                      <div className="d-flex justify-content-end gap-2 mt-3">
                        <button
                          type="button"
                          className="btn btn-outline-secondary btn-sm"
                          onClick={() => setCreateOpen(false)}
                          disabled={busy === 'create'}
                        >
                          {tr('Cancel')}
                        </button>
                        <button
                          type="submit"
                          className="btn btn-primary btn-sm"
                          disabled={busy === 'create' || !label.trim()}
                        >
                          {busy === 'create' ? tr('Creating…') : tr('Create key')}
                        </button>
                      </div>
                    </form>
                  ) : null}

                  {keys.data.keys.length === 0 ? (
                    <EmptyState
                      message={
                        showAll
                          ? 'No gateway key has been created yet.'
                          : 'You have not created a gateway key yet.'
                      }
                    />
                  ) : (
                    <div className="table-responsive">
                      <table
                        className="table table-striped align-middle mb-0"
                        style={{ minWidth: showAll ? '56rem' : '48rem' }}
                      >
                        <thead>
                          <tr>
                            <th scope="col">{tr('Label')}</th>
                            {showAll ? <th scope="col">{tr('Owner')}</th> : null}
                            <th scope="col">{tr('Key id')}</th>
                            <th scope="col">{tr('Allowed routes')}</th>
                            <th scope="col">{tr('Created')}</th>
                            <th scope="col">{tr('Status')}</th>
                            <th scope="col" className="text-end">
                              {tr('Actions')}
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {keys.data.keys.map((key) => (
                            <tr key={key.id} className={key.revoked_at ? 'text-secondary' : ''}>
                              <td className="fw-semibold">{key.label}</td>
                              {showAll ? (
                                <td>{key.owner_name || '—'}</td>
                              ) : null}
                              <td>
                                <code>{key.id}</code>
                              </td>
                              <td>
                                {key.routes.length === 0 ? (
                                  <span className="small">{tr('All routes')}</span>
                                ) : (
                                  key.routes.map((name) => (
                                    <code className="d-block small" key={name}>
                                      {name}
                                    </code>
                                  ))
                                )}
                              </td>
                              <td className="text-nowrap text-secondary">
                                {formatDateTime(key.created_at)}
                              </td>
                              <td>
                                <span
                                  className={`badge ${
                                    key.revoked_at ? 'text-bg-secondary' : 'text-bg-success'
                                  }`}
                                >
                                  {key.revoked_at ? tr('Revoked') : tr('Active')}
                                </span>
                              </td>
                              <td className="text-end text-nowrap">
                                <button
                                  type="button"
                                  className="btn btn-outline-danger btn-sm"
                                  disabled={Boolean(key.revoked_at) || busy === `revoke-${key.id}`}
                                  onClick={() => void revokeKey(key)}
                                >
                                  {tr('Revoke')}
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
