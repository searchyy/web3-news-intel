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
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
