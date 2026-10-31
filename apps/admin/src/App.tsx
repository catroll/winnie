import { Route, Routes } from 'react-router-dom';

import { AuthProvider } from './auth/AuthContext';
import { PublicOnly, RequireAuth } from './auth/guards';
import { AppLayout } from './components/AppLayout';
import { AccountPage } from './pages/AccountPage';
import { DashboardPage } from './pages/DashboardPage';
import { IndexPage } from './pages/IndexPage';
import { LoginPage } from './pages/LoginPage';
import { MeetingDetailPage } from './pages/MeetingDetailPage';
import { MeetingsPage } from './pages/MeetingsPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { RoutesPage } from './pages/RoutesPage';
import { TasksPage } from './pages/TasksPage';

export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route element={<PublicOnly />}>
          <Route path="/login" element={<LoginPage />} />
        </Route>

        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="/meetings" element={<MeetingsPage />} />
            <Route path="/meetings/:meetingId" element={<MeetingDetailPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/gateway" element={<RoutesPage />} />
            <Route path="/index" element={<IndexPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
