import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useAuth } from './auth/authContext';
import { RequireAuth } from './auth/RequireAuth';
import { RiskOfBiasScreen } from './features/appraisal/RiskOfBiasScreen';
import { ExtractionScreen } from './features/extraction/ExtractionScreen';
import { PrismaScreen } from './features/prisma/PrismaScreen';
import { ProjectRoute } from './features/project/ProjectLayout';
import { ExtractionFieldsScreen } from './features/protocol/ExtractionFieldsScreen';
import { AnalysisPlanScreen } from './features/protocol/AnalysisPlanScreen';
import { ProtocolDocumentScreen } from './features/protocol/ProtocolDocumentScreen';
import { ProtocolScreen } from './features/protocol/ProtocolScreen';
import { QuestionScreen } from './features/question/QuestionScreen';
import { RegistrationScreen } from './features/registration/RegistrationScreen';
import { SetupScreen } from './features/protocol/SetupScreen';
import { ScreeningScreen } from './features/screening/ScreeningScreen';
import { DeduplicationScreen } from './features/search/DeduplicationScreen';
import { SearchScreen } from './features/search/SearchScreen';
import { SynthesisScreen } from './features/synthesis/SynthesisScreen';
import { TopicScreen } from './features/topic/TopicScreen';
import { DashboardPage } from './pages/DashboardPage';
import { LoginPage } from './pages/LoginPage';
import { SettingsPage } from './pages/SettingsPage';

// The only place that leaves /login after signing in, so there is no race between competing redirects.
function LoginRoute() {
  const { token } = useAuth();
  const location = useLocation();
  if (!token) return <LoginPage />;
  const from = (location.state as { from?: string } | null)?.from;
  return <Navigate to={from && from !== '/login' ? from : '/'} replace />;
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/projects/:projectId" element={<ProjectRoute />}>
          <Route index element={<Navigate to="setup" replace />} />
          <Route path="setup" element={<SetupScreen />} />
          <Route path="topic" element={<TopicScreen />} />
          <Route path="question" element={<QuestionScreen />} />
          <Route path="protocol" element={<ProtocolScreen />} />
          <Route path="analysis-plan" element={<AnalysisPlanScreen />} />
          <Route path="protocol-document" element={<ProtocolDocumentScreen />} />
          <Route path="registration" element={<RegistrationScreen />} />
          <Route path="search" element={<SearchScreen />} />
          <Route path="deduplication" element={<DeduplicationScreen />} />
          <Route path="screening" element={<ScreeningScreen />} />
          <Route path="extraction-fields" element={<ExtractionFieldsScreen />} />
          <Route path="extraction" element={<ExtractionScreen />} />
          <Route path="prisma" element={<PrismaScreen />} />
          <Route path="risk-of-bias" element={<RiskOfBiasScreen />} />
          <Route path="synthesis" element={<SynthesisScreen />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
