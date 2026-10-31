import { Link } from 'react-router-dom';

import { PageHeader } from '../components/PageHeader';

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Not found" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="card">
            <div className="card-body text-center py-5">
              <i className="bi bi-compass fs-1 text-secondary d-block mb-3" aria-hidden="true" />
              <h2 className="h4">This page does not exist</h2>
              <p className="text-secondary">
                The address may be mistyped, or the page may have moved.
              </p>
              <Link to="/" className="btn btn-primary">
                Back to dashboard
              </Link>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
