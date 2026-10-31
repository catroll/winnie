import { ApiError } from '../api/client';
import { tr } from '../i18n';

export function LoadingBlock({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="card">
      <div className="card-body py-5 text-center" role="status" aria-live="polite">
        <div className="spinner-border text-primary" aria-hidden="true" />
        <p className="mt-3 mb-0 text-secondary">{tr(label)}</p>
      </div>
    </div>
  );
}

interface ErrorBlockProps {
  error: Error;
  onRetry?: () => void;
}

export function ErrorBlock({ error, onRetry }: ErrorBlockProps) {
  const isUnauthorized = error instanceof ApiError && error.status === 401;
  const detail =
    error instanceof ApiError
      ? error.detail
      : error.message || tr('Something went wrong.');

  return (
    <div className="alert alert-danger" role="alert">
      <div className="d-flex flex-wrap align-items-center gap-2">
        <i className="bi bi-exclamation-triangle-fill" aria-hidden="true" />
        <span className="flex-grow-1">
          {isUnauthorized ? tr('Your session has expired. Sign in again.') : detail}
        </span>
        {onRetry && !isUnauthorized ? (
          <button type="button" className="btn btn-sm btn-outline-danger" onClick={onRetry}>
            {tr('Retry')}
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="text-center text-secondary py-5">
      <i className="bi bi-inbox fs-1 d-block mb-2" aria-hidden="true" />
      <p className="mb-0">{tr(message)}</p>
    </div>
  );
}
