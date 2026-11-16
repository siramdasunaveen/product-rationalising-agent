// Dev-only token storage for the standalone shell.
const KEY = "pra.dev.token";

export const tokenStore = {
  get: (): string | null => sessionStorage.getItem(KEY),
  set: (t: string) => sessionStorage.setItem(KEY, t),
  clear: () => sessionStorage.removeItem(KEY),
};
