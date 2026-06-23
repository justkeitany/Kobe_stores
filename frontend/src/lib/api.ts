import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
});

// Attach JWT token to every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Auto-refresh on 401
api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;
      const refresh = localStorage.getItem("refresh_token");
      if (refresh) {
        try {
          const res = await axios.post("/api/auth/refresh", { refresh_token: refresh });
          localStorage.setItem("access_token", res.data.access_token);
          localStorage.setItem("refresh_token", res.data.refresh_token);
          original.headers.Authorization = `Bearer ${res.data.access_token}`;
          return api(original);
        } catch {
          localStorage.removeItem("access_token");
          localStorage.removeItem("refresh_token");
          window.location.href = "/login";
        }
      } else {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// Public Xtream/player base URL.
// - If a domain is configured (server_url set), use it as-is (e.g. https://tv.example.com).
// - Otherwise (IP mode) players connect on the dedicated Xtream port 8080, even though
//   the dashboard itself is served on 25461.
export const XTREAM_PORT = 8080;

export function xtreamBaseUrl(serverUrl?: string | null): string {
  if (serverUrl && serverUrl.trim()) return serverUrl.trim().replace(/\/+$/, "");
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:${XTREAM_PORT}`;
}

// Mint an encrypted, expiring play token for the web player. Keeps the upstream
// URL and credentials out of the shareable /watch link.
export async function mintStreamToken(req: { stream_id?: number; url?: string }): Promise<string> {
  const { data } = await api.post("/stream/token", req);
  return data.token as string;
}

export default api;
