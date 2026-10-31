import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './locales/en.json';
import zhCN from './locales/zh-CN.json';

export const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'zh-CN', label: '中文' },
] as const;

const STORAGE_KEY = 'winnie.admin.language';

function detectLanguage(): string {
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored && LANGUAGES.some((language) => language.code === stored)) {
    return stored;
  }
  const preferred = navigator.languages?.find((language) =>
    language.toLowerCase().startsWith('zh'),
  );
  return preferred ? 'zh-CN' : 'en';
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    'zh-CN': { translation: zhCN },
  },
  lng: detectLanguage(),
  fallbackLng: 'en',
  supportedLngs: LANGUAGES.map((language) => language.code),
  // UI copy is the key. Page labels are plain English strings, so dots and
  // colons in a label must not be parsed as namespace separators.
  keySeparator: false,
  nsSeparator: false,
  interpolation: { escapeValue: false },
  returnNull: false,
});

document.documentElement.lang = i18n.resolvedLanguage ?? 'en';

i18n.on('languageChanged', (language) => {
  window.localStorage.setItem(STORAGE_KEY, language);
  document.documentElement.lang = language;
});

export function tr(
  key: string,
  options?: Record<string, unknown>,
): string {
  return i18n.t(key, options);
}

export default i18n;
