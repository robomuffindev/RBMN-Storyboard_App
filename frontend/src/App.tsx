import { Routes, Route, Navigate } from 'react-router-dom';
import { useJobEvents } from '@/hooks/useJobEvents';
import ErrorBoundary from '@/components/ErrorBoundary';
import HomePage from '@/components/Layout/HomePage';
import ProjectList from '@/components/Layout/ProjectList';
import AppLayout from '@/components/Layout/AppLayout';
import SettingsPage from '@/components/Settings/SettingsPage';
import BatchPreviewPIP from '@/components/BatchPreviewPIP/BatchPreviewPIP';
import BatchesDashboard from '@/components/BatchMode/BatchesDashboard';
import BatchRunDetail from '@/components/BatchMode/BatchRunDetail';

function App() {
  useJobEvents();

  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/projects" element={<ProjectList />} />
        <Route path="/project/:id" element={<AppLayout />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/batches" element={<BatchesDashboard />} />
        <Route path="/batches/:batchRunId" element={<BatchRunDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <BatchPreviewPIP />
    </ErrorBoundary>
  );
}

export default App;
