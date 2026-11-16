// Client-side mirror of the Define Scope rules (backend app/domain/runs.py is authoritative).
import type { RunScope } from "../api/runs";

export const LIMITS = { nameMin: 3, nameMax: 120, marketsMax: 10, familiesMax: 100, maxMonths: 36 };

export function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function addMonths(iso: string, months: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const total = y * 12 + (m - 1) + months;
  const year = Math.floor(total / 12);
  const month = (total % 12) + 1;
  const last = new Date(year, month, 0).getDate();
  return isoDate(new Date(year, month - 1, Math.min(d, last)));
}

export function last12Months(today = new Date()): { start: string; end: string } {
  const end = isoDate(today);
  return { start: addMonths(end, -12), end };
}

export const normaliseName = (s: string) => s.split(/\s+/).filter(Boolean).join(" ");

export function validateScope(s: RunScope): Record<string, string> {
  const e: Record<string, string> = {};
  const name = normaliseName(s.name);
  if (!name) e.name = "Run name is required";
  else if (name.length < LIMITS.nameMin || name.length > LIMITS.nameMax)
    e.name = `Run name must be ${LIMITS.nameMin}–${LIMITS.nameMax} characters`;
  if (!s.portfolio_id) e.portfolio_id = "Portfolio is required";
  if (s.market_ids.length < 1 || s.market_ids.length > LIMITS.marketsMax)
    e.market_ids = `Select 1–${LIMITS.marketsMax} markets`;
  if (s.product_family_ids.length < 1 || s.product_family_ids.length > LIMITS.familiesMax)
    e.product_family_ids = `Select 1–${LIMITS.familiesMax} product families`;
  if (!s.period_start) e.period_start = "Period start is required";
  if (!s.period_end) e.period_end = "Period end is required";
  if (s.period_start && s.period_end) {
    if (s.period_start > s.period_end) e.period_end = "Period end must be on or after the start";
    else if (s.period_end > addMonths(s.period_start, LIMITS.maxMonths))
      e.period_end = `The period can be at most ${LIMITS.maxMonths} months`;
  }
  if (s.objectives.length === 0) e.objectives = "Select at least one business objective";
  if (!s.owner_id) e.owner_id = "Accountable owner is required";
  return e;
}
