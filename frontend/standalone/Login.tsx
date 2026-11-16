import { useState } from "react";
import { API_BASE } from "../src";

/** Standalone sign-in (email + password). In AI Studio the studio's own sign-in is used instead. */
export function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (r.status === 401) throw new Error("Invalid email or password");
      if (!r.ok) throw new Error(`Sign-in is unavailable (HTTP ${r.status}). Is the backend running?`);
      onLogin((await r.json()).access_token);
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg === "Failed to fetch" ? "Cannot reach the server. Is the backend running?" : msg);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="login__card" onSubmit={submit}>
        <div className="brand">
          <span className="brand__logo">DEEP<br />KORCE</span>
          <span>
            <span className="brand__title">Product Rationalisation Agent</span>
            <span className="brand__sub">Telco · Agentstudio</span>
          </span>
        </div>
        <h1 className="login__heading">Sign in</h1>
        <label>
          Email
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <p className="login__error" role="alert">{error}</p>}
        <button className="pra-button pra-button--primary" disabled={busy || !email || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
