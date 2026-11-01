import { Link } from 'react-router-dom';

import { api } from '../api/client';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { useApi } from '../lib/hooks';

export function RoutesPage() {
  const routes = useApi(() => api.routes(), []);

  return (
    <>
      <PageHeader title="Gateway routes" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="alert alert-info" role="note">
            <i className="bi bi-info-circle-fill me-2" aria-hidden="true" />
            {tr(
              'Every path under a route prefix is forwarded unchanged. Callers authenticate with a Winnie key; the route vendor key is read from packages/winnie-llm/.env and injected on the server, so clients never see it.',
            )}{' '}
            {tr('Clients reach a route with a Winnie key; create and revoke keys on')}{' '}
            <Link to="/keys">{tr('API keys')}</Link>.
          </div>

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
            <div className="card">
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
                  <table
                    className="table table-striped align-middle mb-0"
                    style={{ minWidth: '56rem' }}
                  >
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
        </div>
      </div>
    </>
  );
}
