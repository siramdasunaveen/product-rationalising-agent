import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import {
  AssessmentsScreen, CreateRunScopeScreen, EvidenceHubScreen, MappingRulesScreen, PlaceholderScreen, useManifest,
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
  const placeholders = (manifest?.nav ?? []).filter((n) => n.path !== "/");
  return (
    <Routes>
      <Route path="/" element={<AssessmentsRoute />} />
      <Route path="/runs/new" element={<ScopeRoute />} />
      <Route path="/runs/:runId/scope" element={<ScopeRoute />} />
      <Route path="/runs/:runId/evidence" element={<EvidenceRoute />} />
      <Route path="/runs/:runId/evidence/:domain/mapping" element={<MappingRoute />} />
      <Route
        path="/runs/:runId/scoring"
        element={<PlaceholderScreen title="Scoring & AI Route" note="Wizard step 3 arrives in build step 9." />}
      />
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
      onOpenMapping={(domain) => navigate(`/runs/${runId}/evidence/${domain}/mapping`)}
    />
  );
}

function MappingRoute() {
  const { runId, domain } = useParams();
  const navigate = useNavigate();
  return (
    <MappingRulesScreen key={`${runId}-${domain}`} runId={runId!} domain={domain!}
      onBack={() => navigate(`/runs/${runId}/evidence`)} />
  );
}
