/**
 * The API stores UTC. SQLite drops the offset, so timestamps can arrive
 * without a zone designator; treat those as UTC rather than local time.
 */
function parseApiDate(value: string): Date | null {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const date = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value: string | null): string {
  if (!value) {
    return '—';
  }
  const date = parseApiDate(value);
  if (!date) {
    return '—';
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

export function formatDate(value: string | null): string {
  if (!value) {
    return '—';
  }
  const date = parseApiDate(value);
  if (!date) {
    return '—';
  }
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
}

function pad(value: number): string {
  return value.toString().padStart(2, '0');
}

/** Milliseconds to `m:ss`, or `h:mm:ss` once the duration passes an hour. */
export function formatDuration(milliseconds: number | null): string {
  if (milliseconds === null || !Number.isFinite(milliseconds) || milliseconds < 0) {
    return '—';
  }
  const totalSeconds = Math.round(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${pad(minutes)}:${pad(seconds)}`;
  }
  return `${minutes}:${pad(seconds)}`;
}

export function formatSegmentTime(milliseconds: number | null): string {
  return milliseconds === null ? '—' : formatDuration(milliseconds);
}

export function formatLanguage(code: string | null): string {
  if (!code) {
    return 'Unknown';
  }
  const clean = code.split('-')[0].toLowerCase();
  const names: Record<string, string> = {
    zh: 'Chinese',
    en: 'English',
    ja: 'Japanese',
    ko: 'Korean',
    fr: 'French',
    de: 'German',
    es: 'Spanish',
    ru: 'Russian',
    pt: 'Portuguese',
    it: 'Italian',
  };
  return names[clean] ?? code;
}
