import { Route, Routes } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { AuthProvider } from './auth/AuthContext';
import { PublicOnly, RequireAdmin, RequireAuth } from './auth/guards';
import { AppLayout } from './components/AppLayout';
import { AccountPage } from './pages/AccountPage';
import { AIPage } from './pages/AIPage';
import { ChatPage } from './pages/ChatPage';
import { DashboardPage } from './pages/DashboardPage';
import { IndexPage } from './pages/IndexPage';
import { LoginPage } from './pages/LoginPage';
import { MeetingDetailPage } from './pages/MeetingDetailPage';
import { MeetingsPage } from './pages/MeetingsPage';
import { NewsPage } from './pages/NewsPage';
import { PhonePage } from './pages/PhonePage';
import { NotFoundPage } from './pages/NotFoundPage';
import { RoutesPage } from './pages/RoutesPage';
import { SchedulePage } from './pages/SchedulePage';
import { SkillsPage } from './pages/SkillsPage';
import { TasksPage } from './pages/TasksPage';
import { UsagePage } from './pages/UsagePage';
import { UsersPage } from './pages/UsersPage';

export function App() {
  // Subscribe the whole tree to language changes: page labels are translated
  // through the shared `tr()` helper, so a parent re-render is enough.
  useTranslation();

  return (
    <AuthProvider>
      <Routes>
        <Route element={<PublicOnly />}>
          <Route path="/login" element={<LoginPage />} />
        </Route>

        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:conversationId" element={<ChatPage />} />
            <Route path="/news" element={<NewsPage />} />
            <Route path="/schedule" element={<SchedulePage />} />
            <Route path="/phone" element={<PhonePage />} />
            <Route path="/meetings" element={<MeetingsPage />} />
            <Route path="/meetings/:meetingId" element={<MeetingDetailPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/skills" element={<SkillsPage />} />
            <Route path="/usage" element={<UsagePage />} />
            <Route path="/ai" element={<AIPage />} />
            <Route path="/gateway" element={<RoutesPage />} />
            <Route path="/index" element={<IndexPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route element={<RequireAdmin />}>
              <Route path="/users" element={<UsersPage />} />
            </Route>
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
