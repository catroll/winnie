import { useState, type FormEvent } from 'react';

import { ApiError, api } from '../api/client';
import type { GatewayKey } from '../api/types';
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

export function RoutesPage() {
  const routes = useApi(() => api.routes(), []);
  const keys = useApi(() => api.gatewayKeys(), []);
  const [createOpen, setCreateOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [selectedRoutes, setSelectedRoutes] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);

  const routeNames = routes.data?.routes.map((route) => route.name) ?? [];
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
      <PageHeader title="Gateway routes" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="alert alert-info" role="note">
            <i className="bi bi-info-circle-fill me-2" aria-hidden="true" />
            {tr(
              'Every path under a route prefix is forwarded unchanged. Callers authenticate with a Winnie key; the route vendor key is read from packages/winnie-llm/.env and injected on the server, so clients never see it.',
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

          {routes.status === 'loading' ? (
            <LoadingBlock label="Loading routes…" />
          ) : routes.status === 'error' ? (
            <ErrorBlock error={routes.error} onRetry={routes.reload} />
          ) : routes.data.routes.length === 0 ? (
            <div className="card">
              <div className="card-body">
                <EmptyState message="No gateway routes are configured." />
              </div>
            </div>
          ) : (
            <div className="card mb-4">
              <div className="card-header">
                <h3 className="card-title">
                  {routes.data.routes.length === 1
                    ? tr('{{count}} configured route', {
                        count: routes.data.routes.length,
                      })
                    : tr('{{count}} configured routes', {
                        count: routes.data.routes.length,
                      })}
                </h3>
              </div>
              <div className="card-body p-0">
                <div className="table-responsive">
                  <table className="table table-striped align-middle mb-0" style={{ minWidth: '56rem' }}>
                    <thead>
                      <tr>
                        <th scope="col">{tr('Route')}</th>
                        <th scope="col">{tr('Protocol')}</th>
                        <th scope="col">{tr('Upstream')}</th>
                        <th scope="col">{tr('Proxy prefix')}</th>
                        <th scope="col">{tr('Server credential')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {routes.data.routes.map((route) => (
                        <tr key={route.name}>
                          <td className="fw-semibold text-nowrap">{route.name}</td>
                          <td>
                            <span
                              className={`badge ${
                                route.protocol === 'openai' ? 'text-bg-primary' : 'text-bg-dark'
                              }`}
                            >
                              {route.protocol === 'openai' ? 'OpenAI' : 'Anthropic'}
                            </span>
                          </td>
                          <td>
                            <div className="text-break">{route.base_url}</div>
                            <div className="small text-secondary text-break">
                              {tr('API base: {{url}}', { url: route.api_base })}
                            </div>
                          </td>
                          <td>
                            <code className="text-nowrap">{route.path_prefix}</code>
                          </td>
                          <td>
                            {route.credential_present ? (
                              <span className="badge text-bg-success">{route.api_key_env}</span>
                            ) : (
                              <>
                                <span className="badge text-bg-warning">
                                  {tr('Missing {{name}}', { name: route.api_key_env })}
                                </span>
                                <div className="small text-secondary mt-1">
                                  {tr(
                                    'Set it in packages/winnie-llm/.env and restart the API; this route answers 503 until then.',
                                  )}
                                </div>
                              </>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          <div className="card">
            <div className="card-header">
              <h3 className="card-title mb-0">{tr('Gateway keys')}</h3>
              <div className="card-tools">
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
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
                      {tr(
                        'No active gateway key exists. Every call to a route is rejected with 401 until you create one.',
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
                          {routeNames.length === 0 ? (
                            <div className="text-secondary small">
                              {tr('No routes are configured yet.')}
                            </div>
                          ) : (
                            <div className="d-flex flex-wrap gap-3">
                              {routeNames.map((name) => (
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
                    <EmptyState message="No gateway key has been created yet." />
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-striped align-middle mb-0" style={{ minWidth: '48rem' }}>
                        <thead>
                          <tr>
                            <th scope="col">{tr('Label')}</th>
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
