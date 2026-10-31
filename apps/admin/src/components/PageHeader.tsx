import { Link } from 'react-router-dom';

import { tr } from '../i18n';
import { useTitle } from '../lib/hooks';

export interface Crumb {
  label: string;
  to?: string;
}

interface PageHeaderProps {
  title: string;
  crumbs?: Crumb[];
}

export function PageHeader({ title, crumbs = [] }: PageHeaderProps) {
  useTitle(title);
  return (
    <div className="app-content-header">
      <div className="container-fluid">
        <div className="row">
          <div className="col-sm-6">
            <h1 className="mb-0 fs-3">{tr(title)}</h1>
          </div>
          <div className="col-sm-6">
            <nav aria-label="breadcrumb">
              <ol className="breadcrumb float-sm-end">
                <li className="breadcrumb-item">
                  <Link to="/">{tr('Home')}</Link>
                </li>
                {crumbs.map((crumb) => (
                  <li key={crumb.label} className="breadcrumb-item">
                    {crumb.to ? <Link to={crumb.to}>{tr(crumb.label)}</Link> : tr(crumb.label)}
                  </li>
                ))}
                <li className="breadcrumb-item active" aria-current="page">
                  {tr(title)}
                </li>
              </ol>
            </nav>
          </div>
        </div>
      </div>
    </div>
  );
}
