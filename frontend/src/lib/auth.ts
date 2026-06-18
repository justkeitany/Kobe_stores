import api from "./api";

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  must_change_password: boolean;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const form = new URLSearchParams();
  form.append("username", username);
  form.append("password", password);

  const res = await api.post<LoginResponse>("/auth/login", form, {
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });

  localStorage.setItem("access_token", res.data.access_token);
  localStorage.setItem("refresh_token", res.data.refresh_token);
  return res.data;
}

export function logout() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  window.location.href = "/login";
}

export function isAuthenticated(): boolean {
  return !!localStorage.getItem("access_token");
}

/** Decode the logged-in username (JWT `sub` claim). Falls back to "admin". */
export function currentUsername(): string {
  const token = localStorage.getItem("access_token");
  if (!token) return "admin";
  try {
    const payload = JSON.parse(atob(token.split(".")[1] ?? ""));
    return payload?.sub || "admin";
  } catch {
    return "admin";
  }
}

/** Decode the role claim (e.g. "admin"). */
export function currentRole(): string {
  const token = localStorage.getItem("access_token");
  if (!token) return "";
  try {
    const payload = JSON.parse(atob(token.split(".")[1] ?? ""));
    return payload?.role || "";
  } catch {
    return "";
  }
}
