import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from '@/components/layout'
import { Dashboard } from '@/features/dashboard/Dashboard'
import { VideoUploadPage } from '@/features/video-upload/VideoUploadPage'
import { VideoLibraryPage } from '@/features/video-library/VideoLibraryPage'
import { VideoDetailPage } from '@/features/video-library/VideoDetailPage'
import { PipelineProgressPage } from '@/features/pipeline-monitor/PipelineProgressPage'
import { RAGChatPage } from '@/features/rag-chat/RAGChatPage'
import { HealthDashboardPage } from '@/features/health-dashboard/HealthDashboardPage'
import { AgentAnalysisPage } from '@/features/agent-analysis/AgentAnalysisPage'
import { SettingsPage } from '@/features/settings/SettingsPage'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="upload" element={<VideoUploadPage />} />
        <Route path="videos" element={<VideoLibraryPage />} />
        <Route path="videos/:id" element={<VideoDetailPage />} />
        <Route path="videos/:id/progress" element={<PipelineProgressPage />} />
        <Route path="chat" element={<RAGChatPage />} />
        <Route path="analysis" element={<AgentAnalysisPage />} />
        <Route path="health" element={<HealthDashboardPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
