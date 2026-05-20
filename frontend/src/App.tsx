import { Routes, Route, Navigate } from 'react-router-dom';
import { useJobEvents } from '@/hooks/useJobEvents';
import HomePage from '@/components/Layout/HomePage';
import ProjectList from '@/components/Layout/ProjectList';
import AppLayout from '@/components/Layout/AppLayout';
import SettingsPage from '@/components/Settings/SettingsPage';

function App() {
  useJobEvents();

  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/projects" element={<ProjectList />} />
      <Route path="/project/:id" element={<AppLayout />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
