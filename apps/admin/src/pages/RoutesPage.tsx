import { api } from '../api/client';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { useApi } from '../lib/hooks';

export function RoutesPage() {
  const result = useApi(() => api.routes(), []);

  return (
    <>
      <PageHeader title="Gateway routes" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="alert alert-info" role="note">
            <i className="bi bi-info-circle-fill me-2" aria-hidden="true" />
            OpenAI and Anthropic providers managed on <strong>AI providers</strong> appear
            here as routes; disable or delete one there to remove its path. Winnie never
            stores vendor API keys: clients pass their own key on every request to the
            proxy path.
          </div>

          {result.status === 'loading' ? (
            <LoadingBlock label="Loading routes…" />
          ) : result.status === 'error' ? (
            <ErrorBlock error={result.error} onRetry={result.reload} />
          ) : result.data.routes.length === 0 ? (
            <div className="card">
              <div className="card-body">
                <EmptyState message="No gateway routes are configured." />
              </div>
            </div>
          ) : (
            <div className="card">
              <div className="card-header">
                <h3 className="card-title">
                  {result.data.routes.length} configured route
                  {result.data.routes.length === 1 ? '' : 's'}
                </h3>
              </div>
              <div className="card-body p-0">
                <div className="table-responsive">
                  <table className="table table-striped align-middle mb-0">
                    <thead>
                      <tr>
                        <th scope="col">Route</th>
                        <th scope="col">Protocol</th>
                        <th scope="col">Upstream</th>
                        <th scope="col">Proxy paths</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.data.routes.map((route) => (
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
                              API base: {route.api_base}
                            </div>
                          </td>
                          <td>
                            {route.paths.map((path) => (
                              <code className="d-block" key={path}>
                                {path}
                              </code>
                            ))}
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
