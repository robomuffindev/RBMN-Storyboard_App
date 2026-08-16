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
import ShortcodeRedirect from '@/components/Chapters/ShortcodeRedirect';
import CharacterStudioPage from './components/CharacterStudio/CharacterStudioPage';
import StoryWorldPage from './components/StoryWorld/StoryWorldPage';
import VideoLabPage from './components/VideoLab/VideoLabPage';
import AudioLabPage from './components/AudioLab/AudioLabPage';
import ToolsPage from './components/Tools/ToolsPage';
import ImageWorkshopPage from './components/ImageWorkshop/ImageWorkshopPage';
import VNCCSNativePage from './components/VNCCSNative/VNCCSNativePage';
import StoryboardPage from './components/Storyboard/StoryboardPage';
import MobileProjects from './components/Mobile/MobileProjects';
import MobileProject from './components/Mobile/MobileProject';
import MobileScenes from './components/Mobile/MobileScenes';
import MobileCharacters from './components/Mobile/MobileCharacters';
import MobileQueue from './components/Mobile/MobileQueue';
import MobileBatchDetail from './components/Mobile/MobileBatchDetail';

function App() {
  useJobEvents();

  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/projects" element={<ProjectList />} />
        <Route path="/project/:id" element={<AppLayout />} />
        <Route path="/project/:id/c/:chapterShortCode" element={<AppLayout />} />
        <Route path="/project/:id/storyboard" element={<StoryboardPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/studio" element={<CharacterStudioPage />} />
        <Route path="/worlds" element={<StoryWorldPage />} />
        <Route path="/video-lab" element={<VideoLabPage />} />
        <Route path="/audio-lab" element={<AudioLabPage />} />
        <Route path="/studio/vnccs" element={<VNCCSNativePage />} />
        <Route path="/studio/vnccs-klein" element={<VNCCSNativePage variant="klein" />} />
        <Route path="/tools" element={<ToolsPage />} />
        <Route path="/image-workshop" element={<ImageWorkshopPage />} />
        <Route path="/batches" element={<BatchesDashboard />} />
        <Route path="/batches/:batchRunId" element={<BatchRunDetail />} />
        <Route path="/s/:code" element={<ShortcodeRedirect />} />
        <Route path="/mobile" element={<MobileProjects />} />
        <Route path="/mobile/p/:id" element={<MobileProject />} />
        <Route path="/mobile/p/:id/scenes" element={<MobileScenes />} />
        <Route path="/mobile/p/:id/characters" element={<MobileCharacters />} />
        <Route path="/mobile/p/:id/queue" element={<MobileQueue />} />
        <Route path="/mobile/batch/:batchRunId" element={<MobileBatchDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <BatchPreviewPIP />
    </ErrorBoundary>
  );
}

export default App;
