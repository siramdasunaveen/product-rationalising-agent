import { useQuery } from "@tanstack/react-query";
import { apiFetch, qs } from "./client";

export type Portfolio = { id: string; code: string; name: string };
export type Market = { id: string; code: string; name: string; timezone: string };
export type ProductFamily = { id: string; code: string; name: string; portfolio_id: string; parent_id: string | null };
export type Objective = { key: string; label: string };
export type Person = { user_id: string; name: string; email: string | null; roles: string[] };

const STATIC = { staleTime: 5 * 60_000 };

export const usePortfolios = () =>
  useQuery({ queryKey: ["meta", "portfolios"], queryFn: () => apiFetch<Portfolio[]>("/metadata/portfolios"), ...STATIC });

export const useMarkets = () =>
  useQuery({ queryKey: ["meta", "markets"], queryFn: () => apiFetch<Market[]>("/metadata/markets"), ...STATIC });

export const useProductFamilies = (portfolioId: string | null) =>
  useQuery({
    queryKey: ["meta", "families", portfolioId],
    queryFn: () => apiFetch<ProductFamily[]>(`/metadata/product-families${qs({ portfolio_id: portfolioId ?? undefined })}`),
    enabled: !!portfolioId,
    ...STATIC,
  });

export const useObjectives = () =>
  useQuery({ queryKey: ["meta", "objectives"], queryFn: () => apiFetch<Objective[]>("/metadata/objectives"), ...STATIC });

export const OWNER_ROLES = ["portfolio_manager", "product_owner"];

export const useOwners = () =>
  useQuery({
    queryKey: ["people", "owners"],
    queryFn: () => apiFetch<Person[]>(`/people${qs({ role: OWNER_ROLES })}`),
    ...STATIC,
  });
