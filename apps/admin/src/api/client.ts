import type {
  AIIntegration,
  AIIntegrationInput,
  AIProviderConfig,
  AIVendor,
  AIVendorInput,
  AssistantChatResponse,
  AssistantConversation,
  AssistantConversationList,
  AssistantSkill,
  AssistantSkillList,
  AssistantStatus,
  AssistantTaskList,
  AssistantTaskRunList,
  Health,
  IndexConfigResponse,
  IndexEditableConfig,
  IndexBuildResult,
  IndexSearchResponse,
  IndexStatus,
  Meeting,
  MeetingList,
  NewsDraftDetail,
  NewsEventList,
  NewsRun,
  NewsRunDrafts,
  NewsSourceList,
  NewsStatus,
  Minutes,
  ModelRuntime,
  Readiness,
  RouteList,
  TokenResponse,
  Transcript,
  UsageBalanceList,
  UsageOverview,
  UsageRecordList,
  User,
} from './types';
import { getToken } from '../auth/storage';

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, detail: string, retryAfterSeconds: number | null = null) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Called when a request that carried a token is answered with 401. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  /** Do not attach the bearer token; used by the login request. */
  anonymous?: boolean;
}

function parseRetryAfter(header: string | null): number | null {
  if (!header) {
    return null;
  }
  const seconds = Number(header);
  return Number.isFinite(seconds) && seconds >= 0 ? Math.ceil(seconds) : null;
}

function describeDetail(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          item && typeof item === 'object' && 'msg' in item
            ? String((item as { msg: unknown }).msg)
            : null,
        )
        .filter((message): message is string => Boolean(message));
      if (messages.length > 0) {
        return messages.join('; ');
      }
    }
  }
  return fallback;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' });
  const body =
    options.body === undefined ? undefined : JSON.stringify(options.body);
  if (body !== undefined) {
    headers.set('Content-Type', 'application/json');
  }

  const token = options.anonymous ? null : getToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    throw new ApiError(
      0,
      'Cannot reach the Winnie API. Check that the server is running and reachable.',
    );
  }

  if (response.status === 401 && token) {
    unauthorizedHandler?.();
  }

  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      // A non-JSON error body still carries a useful status.
    }
    throw new ApiError(
      response.status,
      describeDetail(payload, `Request failed with HTTP ${response.status}.`),
      parseRetryAfter(response.headers.get('Retry-After')),
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function parseFilename(header: string | null, fallback: string): string {
  if (!header) {
    return fallback;
  }
  const quoted = /filename="([^"]+)"/i.exec(header);
  if (quoted?.[1]) {
    return quoted[1];
  }
  const bare = /filename=([^;]+)/i.exec(header);
  return bare?.[1]?.trim() || fallback;
}

export const api = {
  health(): Promise<Health> {
    return request<Health>('/healthz');
  },

  readiness(): Promise<Readiness> {
    return request<Readiness>('/readyz');
  },

  routes(): Promise<RouteList> {
    return request<RouteList>('/routes');
  },

  indexStatus(): Promise<IndexStatus> {
    return request<IndexStatus>('/api/v1/index/status');
  },

  indexConfig(): Promise<IndexConfigResponse> {
    return request<IndexConfigResponse>('/api/v1/index/config');
  },

  saveIndexConfig(
    config: IndexEditableConfig,
  ): Promise<{ config: IndexEditableConfig; roots: string[] }> {
    return request('/api/v1/index/config', { method: 'PUT', body: { config } });
  },

  searchIndex(payload: {
    query: string;
    roots?: string[];
    since?: string;
    limit?: number;
  }): Promise<IndexSearchResponse> {
    return request('/api/v1/index/search', { method: 'POST', body: payload });
  },

  buildIndex(dryRun: boolean): Promise<{ build: IndexBuildResult }> {
    return request('/api/v1/index/build', {
      method: 'POST',
      body: { dry_run: dryRun },
    });
  },

  indexSkill(action: 'install' | 'check'): Promise<{ ok: boolean; path: string }> {
    return request('/api/v1/index/skill', { method: 'POST', body: { action } });
  },

  aiConfig(): Promise<AIProviderConfig> {
    return request<AIProviderConfig>('/api/v1/ai/config');
  },

  createAIVendor(input: AIVendorInput): Promise<{ vendor: AIVendor }> {
    return request('/api/v1/ai/vendors', { method: 'POST', body: input });
  },

  updateAIVendor(
    vendorId: string,
    input: AIVendorInput,
  ): Promise<{ vendor: AIVendor }> {
    return request(`/api/v1/ai/vendors/${encodeURIComponent(vendorId)}`, {
      method: 'PUT',
      body: input,
    });
  },

  syncAIVendor(vendorId: string): Promise<{ vendor: AIVendor }> {
    return request(
      `/api/v1/ai/vendors/${encodeURIComponent(vendorId)}/models/sync`,
      { method: 'POST' },
    );
  },

  createAIIntegration(
    input: AIIntegrationInput,
  ): Promise<{ integration: AIIntegration }> {
    return request('/api/v1/ai/integrations', { method: 'POST', body: input });
  },

  updateAIIntegration(
    integrationId: string,
    input: AIIntegrationInput,
  ): Promise<{ integration: AIIntegration }> {
    return request(`/api/v1/ai/integrations/${encodeURIComponent(integrationId)}`, {
      method: 'PUT',
      body: input,
    });
  },

  deleteAIIntegration(
    integrationId: string,
  ): Promise<{ name: string; cleared_features: string[] }> {
    return request(`/api/v1/ai/integrations/${encodeURIComponent(integrationId)}`, {
      method: 'DELETE',
    });
  },

  setAIRuntime(runtime: ModelRuntime): Promise<{ runtime: ModelRuntime }> {
    return request('/api/v1/ai/runtime', { method: 'PUT', body: runtime });
  },

  setAIFeature(
    feature: string,
    binding: { vendor: string; model: string },
  ): Promise<{ feature: string; binding: { vendor: string; model: string } }> {
    return request(`/api/v1/ai/features/${encodeURIComponent(feature)}`, {
      method: 'PUT',
      body: binding,
    });
  },

  assistantTasks(): Promise<AssistantTaskList> {
    return request<AssistantTaskList>('/api/v1/assistant/tasks');
  },

  assistantStatus(): Promise<AssistantStatus> {
    return request<AssistantStatus>('/api/v1/assistant/status');
  },

  assistantSkills(): Promise<AssistantSkillList> {
    return request<AssistantSkillList>('/api/v1/assistant/skills');
  },

  setAssistantSkill(name: string, enabled: boolean): Promise<AssistantSkill> {
    return request<AssistantSkill>(
      `/api/v1/assistant/skills/${encodeURIComponent(name)}`,
      { method: 'PUT', body: { enabled } },
    );
  },

  assistantTaskRuns(params: {
    task_id?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<AssistantTaskRunList> {
    const search = new URLSearchParams();
    if (params.task_id) {
      search.set('task_id', params.task_id);
    }
    if (params.limit !== undefined) {
      search.set('limit', String(params.limit));
    }
    if (params.offset !== undefined) {
      search.set('offset', String(params.offset));
    }
    const query = search.toString();
    return request<AssistantTaskRunList>(
      `/api/v1/assistant/tasks/runs${query ? `?${query}` : ''}`,
    );
  },

  assistantConversations(params: {
    limit?: number;
    offset?: number;
    channel?: string;
  } = {}): Promise<AssistantConversationList> {
    const search = new URLSearchParams();
    if (params.limit !== undefined) {
      search.set('limit', String(params.limit));
    }
    if (params.offset !== undefined) {
      search.set('offset', String(params.offset));
    }
    if (params.channel) {
      search.set('channel', params.channel);
    }
    const query = search.toString();
    return request<AssistantConversationList>(
      `/api/v1/assistant/conversations${query ? `?${query}` : ''}`,
    );
  },

  assistantConversation(conversationId: string): Promise<AssistantConversation> {
    return request<AssistantConversation>(
      `/api/v1/assistant/conversations/${encodeURIComponent(conversationId)}`,
    );
  },

  assistantChat(payload: {
    message: string;
    conversation_id?: string;
    channel: 'admin' | 'web' | 'api';
  }): Promise<AssistantChatResponse> {
    return request<AssistantChatResponse>('/api/v1/assistant/chat', {
      method: 'POST',
      body: payload,
    });
  },

  usageOverview(days: number): Promise<UsageOverview> {
    return request<UsageOverview>(`/api/v1/usage/overview?days=${days}`);
  },

  usageRecords(params: {
    days: number;
    limit?: number;
    offset?: number;
    capability?: string;
    provider?: string;
    model?: string;
    status?: string;
  }): Promise<UsageRecordList> {
    const search = new URLSearchParams({ days: String(params.days) });
    if (params.limit !== undefined) {
      search.set('limit', String(params.limit));
    }
    if (params.offset !== undefined) {
      search.set('offset', String(params.offset));
    }
    for (const key of ['capability', 'provider', 'model', 'status'] as const) {
      const value = params[key];
      if (value) {
        search.set(key, value);
      }
    }
    return request<UsageRecordList>(`/api/v1/usage/records?${search.toString()}`);
  },

  usageBalances(): Promise<UsageBalanceList> {
    return request<UsageBalanceList>('/api/v1/usage/balances');
  },

  refreshUsageBalances(): Promise<UsageBalanceList> {
    return request<UsageBalanceList>('/api/v1/usage/balances/refresh', {
      method: 'POST',
    });
  },

  login(username: string, password: string): Promise<TokenResponse> {
    return request<TokenResponse>('/api/v1/auth/login', {
      method: 'POST',
      body: { username, password },
      anonymous: true,
    });
  },

  me(): Promise<User> {
    return request<User>('/api/v1/auth/me');
  },

  logout(): Promise<void> {
    return request<void>('/api/v1/auth/logout', { method: 'POST' });
  },

  changePassword(currentPassword: string, newPassword: string): Promise<void> {
    return request<void>('/api/v1/auth/password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    });
  },

  listMeetings(limit: number, offset: number): Promise<MeetingList> {
    return request<MeetingList>(
      `/api/v1/meetings?limit=${limit}&offset=${offset}`,
    );
  },

  getMeeting(meetingId: string): Promise<Meeting> {
    return request<Meeting>(`/api/v1/meetings/${meetingId}`);
  },

  getTranscript(meetingId: string): Promise<Transcript> {
    return request<Transcript>(`/api/v1/meetings/${meetingId}/transcript`);
  },

  getMinutes(meetingId: string): Promise<Minutes> {
    return request<Minutes>(`/api/v1/meetings/${meetingId}/minutes`);
  },

  regenerateMinutes(meetingId: string): Promise<Minutes> {
    return request<Minutes>(
      `/api/v1/meetings/${meetingId}/minutes/regenerate`,
      { method: 'POST' },
    );
  },

  newsStatus(): Promise<NewsStatus> {
    return request<NewsStatus>('/api/v1/news/status');
  },

  newsRuns(limit = 20): Promise<NewsRun[]> {
    return request<NewsRun[]>(`/api/v1/news/runs?limit=${limit}`);
  },

  startNewsRun(): Promise<NewsRun> {
    return request<NewsRun>('/api/v1/news/runs', {
      method: 'POST',
      body: {},
    });
  },

  newsRun(runId: string): Promise<NewsRun> {
    return request<NewsRun>(`/api/v1/news/runs/${encodeURIComponent(runId)}`);
  },

  newsDrafts(runId: string): Promise<NewsRunDrafts> {
    return request<NewsRunDrafts>(
      `/api/v1/news/runs/${encodeURIComponent(runId)}/drafts`,
    );
  },

  newsDraft(runId: string, eventId: string): Promise<NewsDraftDetail> {
    return request<NewsDraftDetail>(
      `/api/v1/news/runs/${encodeURIComponent(runId)}/drafts/${encodeURIComponent(eventId)}`,
    );
  },

  newsSources(): Promise<NewsSourceList> {
    return request<NewsSourceList>('/api/v1/news/sources');
  },

  regenerateNewsDraft(
    runId: string,
    eventId: string,
  ): Promise<NewsDraftDetail> {
    return request<NewsDraftDetail>(
      `/api/v1/news/runs/${encodeURIComponent(runId)}/drafts/${encodeURIComponent(eventId)}/regenerate`,
      { method: 'POST', body: {} },
    );
  },

  newsEvents(limit = 50): Promise<NewsEventList> {
    return request<NewsEventList>(`/api/v1/news/events?limit=${limit}`);
  },

  async newsDraftMarkdown(runId: string, eventId: string): Promise<string> {
    const token = getToken();
    const headers = new Headers();
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }
    let response: Response;
    try {
      response = await fetch(
        `${API_BASE}/api/v1/news/runs/${encodeURIComponent(runId)}/drafts/${encodeURIComponent(eventId)}/markdown`,
        { headers },
      );
    } catch {
      throw new ApiError(
        0,
        'Cannot reach the Winnie API. Check that the server is running and reachable.',
      );
    }
    if (response.status === 401 && token) {
      unauthorizedHandler?.();
    }
    if (!response.ok) {
      throw new ApiError(
        response.status,
        `Draft export failed with HTTP ${response.status}.`,
      );
    }
    return response.text();
  },

  deleteMeeting(meetingId: string): Promise<void> {
    return request<void>(`/api/v1/meetings/${meetingId}`, { method: 'DELETE' });
  },

  async exportMeeting(
    meetingId: string,
    format: 'markdown' | 'txt',
  ): Promise<{ blob: Blob; filename: string }> {
    const token = getToken();
    const headers = new Headers();
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    let response: Response;
    try {
      response = await fetch(
        `${API_BASE}/api/v1/meetings/${meetingId}/export?format=${format}`,
        { headers },
      );
    } catch {
      throw new ApiError(
        0,
        'Cannot reach the Winnie API. Check that the server is running and reachable.',
      );
    }

    if (response.status === 401 && token) {
      unauthorizedHandler?.();
    }
    if (!response.ok) {
      let payload: unknown = null;
      try {
        payload = await response.json();
      } catch {
        // Ignore a non-JSON error body.
      }
      throw new ApiError(
        response.status,
        describeDetail(payload, `Export failed with HTTP ${response.status}.`),
      );
    }

    return {
      blob: await response.blob(),
      filename: parseFilename(
        response.headers.get('Content-Disposition'),
        `meeting.${format === 'markdown' ? 'md' : 'txt'}`,
      ),
    };
  },
};
