// Client d'API : jeton d'accès en mémoire seulement (jamais dans le stockage du navigateur) ;
// le jeton de rafraîchissement est un cookie HttpOnly géré par le serveur.

export interface User {
  id: number;
  username: string;
  role: "admin" | "user";
  must_change_password: boolean;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onSessionLost: (() => void) | null = null;

export function setSessionLostHandler(fn: () => void) {
  onSessionLost = fn;
}

export function setToken(t: string | null) {
  accessToken = t;
}

export function hasToken() {
  return accessToken !== null;
}

async function tryRefresh(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const r = await fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" });
      if (!r.ok) return false;
      const data = await r.json();
      accessToken = data.access_token;
      return true;
    } catch {
      return false;
    } finally {
      setTimeout(() => (refreshing = null), 0);
    }
  })();
  return refreshing;
}

async function raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const r = await fetch(`/api${path}`, { ...init, headers, credentials: "same-origin" });
  if (r.status === 401 && retry && !path.startsWith("/auth/login")) {
    if (await tryRefresh()) return raw(path, init, false);
    accessToken = null;
    onSessionLost?.();
  }
  return r;
}

async function parse<T>(r: Response): Promise<T> {
  if (r.status === 204) return undefined as T;
  const text = await r.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* corps non JSON */
  }
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const msg = typeof detail === "string" ? detail : r.status === 413 ? "Fichier trop volumineux." : "Une erreur est survenue.";
    throw new ApiError(r.status, msg);
  }
  return data as T;
}

function json(method: string, body?: unknown): RequestInit {
  return body === undefined
    ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  get: <T>(p: string) => raw(p).then((r) => parse<T>(r)),
  post: <T>(p: string, body?: unknown) => raw(p, json("POST", body)).then((r) => parse<T>(r)),
  put: <T>(p: string, body?: unknown) => raw(p, json("PUT", body)).then((r) => parse<T>(r)),
  patch: <T>(p: string, body?: unknown) => raw(p, json("PATCH", body)).then((r) => parse<T>(r)),
  del: <T>(p: string) => raw(p, { method: "DELETE" }).then((r) => parse<T>(r)),
  upload: <T>(p: string, form: FormData) => raw(p, { method: "POST", body: form }).then((r) => parse<T>(r)),
  async blob(p: string): Promise<Blob> {
    const r = await raw(p);
    if (!r.ok) throw new ApiError(r.status, "Fichier indisponible.");
    return r.blob();
  },
};

export async function login(username: string, password: string): Promise<User> {
  const r = await raw("/auth/login", json("POST", { username, password }), false);
  const data = await parse<{ access_token: string; user: User }>(r);
  accessToken = data.access_token;
  return data.user;
}

export async function restoreSession(): Promise<User | null> {
  if (!(await tryRefresh())) return null;
  return api.get<User>("/auth/me");
}

export async function logout() {
  try {
    await raw("/auth/logout", { method: "POST" }, false);
  } finally {
    accessToken = null;
  }
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<User> {
  const data = await api.post<{ access_token: string; user: User }>("/auth/change-password", {
    old_password: oldPassword,
    new_password: newPassword,
  });
  accessToken = data.access_token;
  return data.user;
}

export async function downloadFile(path: string, filename: string) {
  const blob = await api.blob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Envoi d'un fichier avec suivi de progression (fetch ne le permet pas). Session renouvelée une fois si expirée. */
export function uploadWithProgress<T>(path: string, form: FormData, onProgress: (sent: number, total: number) => void,
  signal?: AbortSignal): Promise<T> {
  const attempt = (retry: boolean): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api${path}`);
      xhr.withCredentials = true;
      if (accessToken) xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded, e.total);
      xhr.onerror = () => reject(new ApiError(0, "Connexion au serveur impossible."));
      xhr.onabort = () => reject(new ApiError(-1, "Envoi annulé."));
      xhr.onload = () => {
        if (xhr.status === 401 && retry) {
          tryRefresh().then((ok) => {
            if (ok) attempt(false).then(resolve, reject);
            else {
              accessToken = null;
              onSessionLost?.();
              reject(new ApiError(401, "Session expirée."));
            }
          });
          return;
        }
        let data: unknown = null;
        try {
          data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
        } catch {
          /* corps non JSON */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
        else {
          const detail = (data as { detail?: unknown } | null)?.detail;
          reject(new ApiError(xhr.status, typeof detail === "string" ? detail : xhr.status === 413 ? "Fichier trop volumineux." : "Une erreur est survenue."));
        }
      };
      signal?.addEventListener("abort", () => xhr.abort(), { once: true });
      xhr.send(form);
    });
  return attempt(true);
}
