import { Navigate, Route, Routes } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { AuthProvider, useAuth } from './auth/AuthContext';
import { PublicOnly, RequireAdmin, RequireAuth } from './auth/guards';
import { AppLayout } from './components/AppLayout';
import { AccountPage } from './pages/AccountPage';
import { AIPage } from './pages/AIPage';
import { ChatPage } from './pages/ChatPage';
import { DashboardPage } from './pages/DashboardPage';
import { InboxPage } from './pages/InboxPage';
import { IndexPage } from './pages/IndexPage';
import { KeysPage } from './pages/KeysPage';
import { LoginPage } from './pages/LoginPage';
import { MeetingDetailPage } from './pages/MeetingDetailPage';
import { MeetingsPage } from './pages/MeetingsPage';
import { NewsPage } from './pages/NewsPage';
import { PhonePage } from './pages/PhonePage';
import { NotFoundPage } from './pages/NotFoundPage';
import { RoutesPage } from './pages/RoutesPage';
import { SchedulePage } from './pages/SchedulePage';
import { SettingsPage } from './pages/SettingsPage';
import { SkillsPage } from './pages/SkillsPage';
import { TasksPage } from './pages/TasksPage';
import { UsersPage } from './pages/UsersPage';

function HomeRoute() {
  const { isAdmin } = useAuth();
  return isAdmin ? <DashboardPage /> : <Navigate to="/chat" replace />;
}

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
            <Route index element={<HomeRoute />} />
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:conversationId" element={<ChatPage />} />
            <Route path="/phone" element={<PhonePage />} />
            <Route path="/meetings" element={<MeetingsPage />} />
            <Route path="/meetings/:meetingId" element={<MeetingDetailPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/skills" element={<SkillsPage />} />
            <Route path="/keys" element={<KeysPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route element={<RequireAdmin />}>
              <Route path="/schedule" element={<SchedulePage />} />
              {/* The usage report is a section of the AI providers page now;
                  the old route stays so an existing bookmark still lands on it. */}
              <Route path="/usage" element={<Navigate to="/ai?tab=usage" replace />} />
              <Route path="/news" element={<NewsPage />} />
              <Route path="/ai" element={<AIPage />} />
              <Route path="/gateway" element={<RoutesPage />} />
              <Route path="/index" element={<IndexPage />} />
              <Route path="/users" element={<UsersPage />} />
            </Route>
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
