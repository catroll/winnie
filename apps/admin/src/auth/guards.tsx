import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { tr } from '../i18n';
import { LoadingScreen } from '../components/LoadingScreen';

/** Routes that require a valid session. */
export function RequireAuth() {
  const { user, checking } = useAuth();
  const location = useLocation();

  if (checking) {
    return <LoadingScreen label={tr('Checking your session…')} />;
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}

/** Login is pointless once a session exists. */
export function PublicOnly() {
  const { user, checking } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: { pathname?: string; search?: string } } | null)
    ?.from;

  if (checking) {
    return <LoadingScreen label={tr('Checking your session…')} />;
  }
  if (user) {
    return <Navigate to={from?.pathname ?? '/'} replace />;
  }
  return <Outlet />;
}



/** Operator-only routes. The API enforces the same rule; this only explains it. */
export function RequireAdmin() {
  const { user, isAdmin, checking } = useAuth();
  const location = useLocation();

  if (checking) {
    return <LoadingScreen label={tr('Checking your session…')} />;
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  if (!isAdmin) {
    return (
      <div className="app-content">
        <div className="container-fluid">
          <div className="alert alert-warning" role="alert">
            <i className="bi bi-shield-lock me-2" aria-hidden="true" />
            {tr('You do not have permission to view this page.')}
          </div>
        </div>
      </div>
    );
  }
  return <Outlet />;
}
