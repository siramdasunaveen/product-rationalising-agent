import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./client";

export type Me = {
  user_id: string;
  name: string;
  tenant_id: string;
  roles: string[];
  permissions: string[];
  approval_scopes: string[];
  platform_mode: string;
  agent: { id: string; name: string; version: string; industry: string };
};

export type NavItem = {
  key: string;
  label: string;
  path: string;
  permission: string;
  status?: string | null;
  sidebarLabel?: string | null;
};

export type Manifest = { id: string; name: string; version: string; nav: NavItem[] };

export function useMe(enabled = true) {
  return useQuery({ queryKey: ["me"], queryFn: () => apiFetch<Me>("/me"), enabled, staleTime: 60_000 });
}

export function useManifest(enabled = true) {
  return useQuery({
    queryKey: ["manifest"],
    queryFn: () => apiFetch<Manifest>("/manifest"),
    enabled,
    staleTime: Infinity,
  });
}

/** The UI decides what to show from permissions, never from role names. */
export function usePermissions() {
  const { data } = useMe();
  const perms = new Set(data?.permissions ?? []);
  return { has: (p: string) => perms.has(p), loaded: !!data };
}
