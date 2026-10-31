import { useTranslation } from 'react-i18next';

import { LANGUAGES, tr } from './index';

export function LanguageSwitcher() {
  const { i18n } = useTranslation();
  const active = i18n.resolvedLanguage ?? 'en';

  return (
    <li className="nav-item dropdown">
      <a
        className="nav-link"
        href="#"
        id="language-menu"
        aria-label={tr('Language')}
        data-bs-toggle="dropdown"
        aria-expanded="false"
      >
        <i className="bi bi-translate" aria-hidden="true" />
        <span className="d-none d-md-inline ms-1">{active === 'zh-CN' ? '中文' : 'EN'}</span>
      </a>
      <ul
        className="dropdown-menu dropdown-menu-end"
        aria-labelledby="language-menu"
        style={{ minWidth: '8rem' }}
      >
        {LANGUAGES.map((language) => (
          <li key={language.code}>
            <button
              type="button"
              className={`dropdown-item d-flex align-items-center${
                active === language.code ? ' active' : ''
              }`}
              aria-pressed={active === language.code}
              onClick={() => void i18n.changeLanguage(language.code)}
            >
              <i
                className={`bi bi-check-lg me-2${
                  active === language.code ? '' : ' invisible'
                }`}
                aria-hidden="true"
              />
              {language.label}
            </button>
          </li>
        ))}
      </ul>
    </li>
  );
}


export function LanguageSelect({ className = '' }: { className?: string }) {
  const { i18n } = useTranslation();
  const active = i18n.resolvedLanguage ?? 'en';

  return (
    <select
      className={`form-select form-select-sm ${className}`.trim()}
      aria-label={tr('Language')}
      value={active}
      onChange={(event) => void i18n.changeLanguage(event.target.value)}
    >
      {LANGUAGES.map((language) => (
        <option key={language.code} value={language.code}>
          {language.label}
        </option>
      ))}
    </select>
  );
}
