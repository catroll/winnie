import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import '@fontsource/source-sans-3/300.css';
import '@fontsource/source-sans-3/400.css';
import '@fontsource/source-sans-3/600.css';
import '@fontsource/source-sans-3/700.css';
import 'bootstrap-icons/font/bootstrap-icons.min.css';
import 'admin-lte/dist/css/adminlte.min.css';
// Bootstrap's JavaScript provides the dropdown, modal and collapse data API
// that AdminLTE markup relies on. AdminLTE's CSS already bundles Bootstrap, so
// only the script is imported here.
import 'bootstrap/dist/js/bootstrap.bundle.min.js';
import './styles.css';

import { App } from './App';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root element not found');
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
