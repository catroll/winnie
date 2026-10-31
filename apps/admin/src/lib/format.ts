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

/** Elapsed time between two API timestamps, for example `6.7 s` or `1 m 12 s`. */
export function formatElapsed(start: string, end: string): string {
  const from = parseApiDate(start);
  const to = parseApiDate(end);
  if (!from || !to) {
    return '—';
  }
  const milliseconds = to.getTime() - from.getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) {
    return '—';
  }
  if (milliseconds < 1000) {
    return `${Math.round(milliseconds)} ms`;
  }
  const seconds = milliseconds / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(1)} s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} m ${Math.round(seconds % 60)} s`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} m`;
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
