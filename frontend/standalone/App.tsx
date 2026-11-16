import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";
import { AssessmentsScreen, PlaceholderScreen, useManifest } from "../src";
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
      <Route path="/" element={<AssessmentsScreen />} />
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
