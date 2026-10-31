import { useEffect } from 'react';
import { initialize } from 'admin-lte';
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';

const NAVIGATION = [
  { to: '/', label: 'Dashboard', icon: 'bi-speedometer2', end: true },
  { to: '/meetings', label: 'Meetings', icon: 'bi-camera-video', end: false },
  { to: '/gateway', label: 'Gateway routes', icon: 'bi-signpost-split', end: false },
  { to: '/account', label: 'Account', icon: 'bi-person-gear', end: false },
];

function closeMobileSidebar() {
  document.body.classList.remove('sidebar-open');
}

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  // AdminLTE initialises its own widgets (push menu, treeview, colour mode,
  // accessibility helpers) on DOMContentLoaded. Calling the exported
  // initializer after React has committed the shell makes the order explicit
  // and keeps the sidebar working when the app is mounted later.
  useEffect(() => {
    initialize();
  }, []);

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  const displayName = user?.display_name?.trim() || user?.username || 'Signed in';

  return (
    <div className="app-wrapper">
      <nav className="app-header navbar navbar-expand bg-body">
        <div className="container-fluid">
          <ul className="navbar-nav">
            <li className="nav-item">
              <a
                className="nav-link"
                href="#"
                role="button"
                aria-label="Toggle sidebar"
                data-lte-toggle="sidebar"
              >
                <i className="bi bi-list" aria-hidden="true" />
              </a>
            </li>
            <li className="nav-item d-none d-md-block">
              <span className="nav-link text-secondary">Winnie management console</span>
            </li>
          </ul>

          <ul className="navbar-nav ms-auto">
            <li className="nav-item">
              <a
                className="nav-link"
                href="#"
                aria-label="Toggle fullscreen"
                data-lte-toggle="fullscreen"
              >
                <i data-lte-icon="maximize" className="bi bi-arrows-fullscreen" aria-hidden="true" />
                <i
                  data-lte-icon="minimize"
                  className="bi bi-fullscreen-exit d-none"
                  aria-hidden="true"
                />
              </a>
            </li>

            <li className="nav-item dropdown">
              <a
                className="nav-link"
                href="#"
                id="theme-menu"
                aria-label="Toggle colour scheme"
                data-bs-toggle="dropdown"
                aria-expanded="false"
              >
                <i className="bi bi-sun-fill" data-lte-theme-icon="light" aria-hidden="true" />
                <i
                  className="bi bi-moon-fill d-none"
                  data-lte-theme-icon="dark"
                  aria-hidden="true"
                />
                <i
                  className="bi bi-circle-half d-none"
                  data-lte-theme-icon="auto"
                  aria-hidden="true"
                />
              </a>
              <ul
                className="dropdown-menu dropdown-menu-end"
                aria-labelledby="theme-menu"
                style={{ minWidth: '8rem' }}
              >
                <li>
                  <button
                    type="button"
                    className="dropdown-item d-flex align-items-center"
                    data-bs-theme-value="light"
                    aria-pressed="false"
                  >
                    <i className="bi bi-sun-fill me-2" aria-hidden="true" />
                    Light
                    <i className="bi bi-check-lg ms-auto d-none" aria-hidden="true" />
                  </button>
                </li>
                <li>
                  <button
                    type="button"
                    className="dropdown-item d-flex align-items-center"
                    data-bs-theme-value="dark"
                    aria-pressed="false"
                  >
                    <i className="bi bi-moon-fill me-2" aria-hidden="true" />
                    Dark
                    <i className="bi bi-check-lg ms-auto d-none" aria-hidden="true" />
                  </button>
                </li>
                <li>
                  <button
                    type="button"
                    className="dropdown-item d-flex align-items-center active"
                    data-bs-theme-value="auto"
                    aria-pressed="true"
                  >
                    <i className="bi bi-circle-half me-2" aria-hidden="true" />
                    Auto
                    <i className="bi bi-check-lg ms-auto d-none" aria-hidden="true" />
                  </button>
                </li>
              </ul>
            </li>

            <li className="nav-item dropdown user-menu">
              <a
                href="#"
                className="nav-link dropdown-toggle"
                data-bs-toggle="dropdown"
                aria-expanded="false"
              >
                <i className="bi bi-person-circle me-1" aria-hidden="true" />
                <span className="d-none d-md-inline">{displayName}</span>
                <span className="visually-hidden d-md-none">Account menu</span>
              </a>
              <ul className="dropdown-menu dropdown-menu-end">
                <li className="px-3 py-2">
                  <div className="fw-semibold">{displayName}</div>
                  <div className="small text-secondary">@{user?.username}</div>
                </li>
                <li>
                  <hr className="dropdown-divider" />
                </li>
                <li>
                  <Link className="dropdown-item" to="/account" onClick={closeMobileSidebar}>
                    <i className="bi bi-person-gear me-2" aria-hidden="true" />
                    Account
                  </Link>
                </li>
                <li>
                  <button type="button" className="dropdown-item" onClick={handleLogout}>
                    <i className="bi bi-box-arrow-right me-2" aria-hidden="true" />
                    Sign out
                  </button>
                </li>
              </ul>
            </li>
          </ul>
        </div>
      </nav>

      <aside className="app-sidebar bg-body-secondary shadow" data-bs-theme="dark">
        <div className="sidebar-brand">
          <Link to="/" className="brand-link" onClick={closeMobileSidebar}>
            <img
              src={`${import.meta.env.BASE_URL}logo.svg`}
              alt=""
              className="brand-image opacity-75 shadow"
            />
            <span className="brand-text fw-light">Winnie Admin</span>
          </Link>
        </div>

        <div className="sidebar-wrapper">
          <nav className="mt-2" aria-label="Main navigation">
            <ul
              className="nav sidebar-menu flex-column"
              id="navigation"
              data-lte-toggle="treeview"
              data-accordion="false"
            >
              {NAVIGATION.map((item) => (
                <li className="nav-item" key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    onClick={closeMobileSidebar}
                    className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
                  >
                    <i className={`nav-icon bi ${item.icon}`} aria-hidden="true" />
                    <p>{item.label}</p>
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </aside>

      <main className="app-main">
        <Outlet />
      </main>

      <footer className="app-footer">
        <div className="float-end d-none d-sm-inline">
          Built on{' '}
          <a
            href="https://adminlte.io"
            className="link-secondary"
            target="_blank"
            rel="noreferrer"
          >
            AdminLTE 4
          </a>
        </div>
        <strong>Winnie</strong> management console
      </footer>
    </div>
  );
}
