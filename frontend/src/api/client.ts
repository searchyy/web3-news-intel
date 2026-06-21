export type ApiOptions = RequestInit & { csrf?: string | null };

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (options.csrf && !headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", options.csrf);
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "include"
  });
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      redirectToLogin();
      throw new Error("Authentication required");
    }
    throw new Error(`HTTP ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function redirectToLogin() {
  if (window.location.pathname === "/login") {
    return;
  }
  if (window.history?.pushState) {
    window.history.pushState({}, "", "/login");
    window.dispatchEvent(new PopStateEvent("popstate"));
    return;
  }
  window.location.assign("/login");
}
