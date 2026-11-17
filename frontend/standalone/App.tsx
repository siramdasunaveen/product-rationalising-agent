import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AIRuntimeScreen, AssessmentsScreen, CandidateExplorerScreen, CandidateReviewScreen, CreateRunScopeScreen,
  EvidenceHubScreen, EvidenceReadinessScreen, MappingRulesScreen, PlaceholderScreen, ReconciliationScreen,
  ReviewStartScreen, RunOverviewScreen, ScoringRouteScreen, useManifest,
} from "../src";
import { tokenStore } from "./auth";
import { Login } from "./Login";
import { Shell } from "./Shell";

export function App() {
  const [token, setToken] = useState(tokenStore.get());
  const queryClient = useQueryClient();

  useEffect(() => {
    const onLogout = () => setToken(null);
    window.addEventListener("pra:logout", onLogout);
    return () => window.removeEventListener("pra:logout", onLogout);
  }, []);

  if (!token) {
    return <Login onLogin={(t) => { tokenStore.set(t); setToken(t); }} />;
  }

  const logout = () => {
    tokenStore.clear();
    queryClient.clear();
    setToken(null);
  };

  return (
    <Shell onLogout={logout}>
      <ScreenRoutes />
    </Shell>
  );
}

function ScreenRoutes() {
  const { data: manifest } = useManifest();
  const built = new Set(["/", "/evidence-readiness", "/ai-runtime"]);
  const placeholders = (manifest?.nav ?? []).filter((n) => !built.has(n.path));
  return (
    <Routes>
      <Route path="/" element={<AssessmentsRoute />} />
      <Route path="/runs/new" element={<ScopeRoute />} />
      <Route path="/runs/:runId/scope" element={<ScopeRoute />} />
      <Route path="/runs/:runId/evidence" element={<EvidenceRoute />} />
      <Route path="/runs/:runId/evidence/:domain/mapping" element={<MappingRoute />} />
      <Route path="/runs/:runId/reconciliation" element={<ReconciliationRoute />} />
      <Route path="/evidence-readiness" element={<ReadinessRoute />} />
      <Route path="/ai-runtime" element={<AIRuntimeScreen />} />
      <Route path="/runs/:runId/readiness" element={<ReadinessRoute />} />
      <Route path="/runs/:runId/scoring" element={<ScoringRoute />} />
      <Route path="/runs/:runId/review" element={<ReviewRoute />} />
      <Route path="/runs/:runId/overview" element={<OverviewRoute />} />
      <Route path="/runs/:runId/candidates" element={<ExplorerRoute />} />
      <Route path="/runs/:runId/candidates/:candidateId" element={<CandidateRoute />} />
      {placeholders.map((n) => (
        <Route
          key={n.key}
          path={n.path}
          element={
            <PlaceholderScreen
              title={n.label}
              note={n.status === "not_specified" ? "Not specified in the user guide yet (see DECISIONS.md)." : undefined}
            />
          }
        />
      ))}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

// Route adapters: the screens take callbacks, the host decides the URLs.
function AssessmentsRoute() {
  const navigate = useNavigate();
  return (
    <AssessmentsScreen
      onStartRun={() => navigate("/runs/new")}
      onOpenRun={(id) => navigate(`/runs/${id}/scope`)}
    />
  );
}

function ScopeRoute() {
  const { runId } = useParams();
  const navigate = useNavigate();
  return (
    <CreateRunScopeScreen
      key={runId ?? "new"}
      runId={runId}
      onCancel={() => navigate("/")}
      onContinue={(run) => navigate(`/runs/${run.id}/evidence`)}
    />
  );
}

function EvidenceRoute() {
  const { runId } = useParams();
  const navigate = useNavigate();
  return (
    <EvidenceHubScreen
      key={runId}
      runId={runId!}
      onBack={() => navigate(`/runs/${runId}/scope`)}
      onContinue={() => navigate(`/runs/${runId}/scoring`)}
      onExit={() => navigate("/")}
      onOpenMapping={(domain, sourceId) =>
        navigate(`/runs/${runId}/evidence/${domain}/mapping${sourceId ? `?source=${sourceId}` : ""}`)}
      onOpenReconciliation={() => navigate(`/runs/${runId}/reconciliation`)}
      onOpenReadiness={() => navigate(`/runs/${runId}/readiness`)}
    />
  );
}

function MappingRoute() {
  const { runId, domain } = useParams();
  const [params] = useSearchParams();
  const source = params.get("source");
  const navigate = useNavigate();
  return (
    <MappingRulesScreen key={`${runId}-${domain}-${source ?? ""}`} runId={runId!} domain={domain!} sourceId={source}
      onBack={() => navigate(`/runs/${runId}/evidence`)} />
  );
}

function ReconciliationRoute() {
  const { runId } = useParams();
  const navigate = useNavigate();
  return (
    <ReconciliationScreen key={runId} runId={runId!} onBack={() => navigate(`/runs/${runId}/evidence`)}
      onOpenReadiness={() => navigate(`/runs/${runId}/readiness`)} />
  );
}

function ReadinessRoute() {
  const { runId } = useParams();
  const navigate = useNavigate();
  return (
    <EvidenceReadinessScreen key={runId ?? "none"} runId={runId}
      onSelectRun={(id) => navigate(`/runs/${id}/readiness`)}
      onOpenEvidence={(id) => navigate(`/runs/${id}/evidence`)}
      onOpenReconciliation={(id) => navigate(`/runs/${id}/reconciliation`)} />
  );
}

function stepPath(runId: string, step: number) {
  return [`/runs/${runId}/scope`, `/runs/${runId}/evidence`, `/runs/${runId}/scoring`, `/runs/${runId}/review`][step - 1];
}

function ScoringRoute() {
  const { runId } = useParams();
  const navigate = useNavigate();
  return (
    <ScoringRouteScreen key={runId} runId={runId!} onBack={() => navigate(`/runs/${runId}/evidence`)}
      onContinue={() => navigate(`/runs/${runId}/review`)} onOpenAIRuntime={() => navigate("/ai-runtime")}
      onSelectStep={(s) => stepPath(runId!, s) && navigate(stepPath(runId!, s)!)} />
  );
}

function ReviewRoute() {
  const { runId } = useParams();
  const navigate = useNavigate();
  return (
    <ReviewStartScreen key={runId} runId={runId!} onEdit={(s) => stepPath(runId!, s) && navigate(stepPath(runId!, s)!)}
      onStarted={() => navigate(`/runs/${runId}/overview`)} onExit={() => navigate("/")} />
  );
}

function ExplorerRoute() {
  const { runId } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return (
    <CandidateExplorerScreen key={runId} runId={runId!} initialView={params.get("view") ?? undefined}
      onOpenCandidate={(id) => navigate(`/runs/${runId}/candidates/${id}`)}
      onOpenOverview={() => navigate(`/runs/${runId}/overview`)} />
  );
}

function CandidateRoute() {
  const { runId, candidateId } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return (
    <CandidateReviewScreen key={candidateId} runId={runId!} candidateRef={candidateId!}
      focusBlockerId={params.get("blockerId") ?? undefined}
      onBack={() => navigate(`/runs/${runId}/candidates`)}
      onContinueToDecision={(id, action, target) =>
        navigate(`/runs/${runId}/candidates/${id}/decision?action=${action}${target ? `&target=${target}` : ""}`)} />
  );
}

function OverviewRoute() {
  const { runId } = useParams();
  const navigate = useNavigate();
  return (
    <RunOverviewScreen key={runId} runId={runId!}
      onOpenCandidates={(view) => navigate(`/runs/${runId}/candidates${view ? `?view=${view}` : ""}`)}
      onOpenCandidate={(id, blockerId) =>
        navigate(`/runs/${runId}/candidates/${id}${blockerId ? `?focus=blocker&blockerId=${blockerId}` : ""}`)}
      onOpenReview={() => navigate(`/runs/${runId}/review`)} onExit={() => navigate("/")} />
  );
}
