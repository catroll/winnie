export interface StatusMeta {
  label: string;
  badge: string;
}

const MEETING_STATUS: Record<string, StatusMeta> = {
  created: { label: 'Created', badge: 'text-bg-secondary' },
  uploaded: { label: 'Audio uploaded', badge: 'text-bg-info' },
  queued: { label: 'Queued', badge: 'text-bg-info' },
  transcribing: { label: 'Transcribing', badge: 'text-bg-warning' },
  transcribed: { label: 'Transcribed', badge: 'text-bg-primary' },
  translating: { label: 'Translating', badge: 'text-bg-warning' },
  summarizing: { label: 'Summarizing', badge: 'text-bg-warning' },
  ready: { label: 'Ready', badge: 'text-bg-success' },
  failed: { label: 'Failed', badge: 'text-bg-danger' },
};

export function meetingStatus(status: string): StatusMeta {
  return MEETING_STATUS[status] ?? { label: status, badge: 'text-bg-secondary' };
}

/** Statuses that still have work left and benefit from a refresh. */
export function isProcessing(status: string): boolean {
  return ['queued', 'transcribing', 'translating', 'summarizing'].includes(status);
}

const TRANSLATION_STATUS: Record<string, StatusMeta> = {
  pending: { label: 'Pending', badge: 'text-bg-secondary' },
  done: { label: 'Translated', badge: 'text-bg-success' },
  skipped: { label: 'Same language', badge: 'text-bg-light text-dark' },
  failed: { label: 'Failed', badge: 'text-bg-danger' },
};

export function translationStatus(status: string): StatusMeta {
  return (
    TRANSLATION_STATUS[status] ?? {
      label: status,
      badge: 'text-bg-secondary',
    }
  );
}

export function sourceLabel(source: string): string {
  return source === 'realtime' ? 'Live recording' : 'File upload';
}

export function stageLabel(stage: string | null): string | null {
  if (!stage) {
    return null;
  }
  const labels: Record<string, string> = {
    upload: 'Upload',
    queued: 'Queue',
    asr: 'Speech recognition',
    translate: 'Translation',
    minutes: 'Minutes',
    done: 'Done',
  };
  return labels[stage] ?? stage;
}
