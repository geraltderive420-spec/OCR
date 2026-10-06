import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import * as client from "../api/client";
import type { User } from "../api/client";

interface AuthState {
  user: User | null;
  ready: boolean;
  login: (u: string, p: string) => Promise<User>;
  logout: () => Promise<void>;
  changePassword: (o: string, n: string) => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const qc = useQueryClient();

  useEffect(() => {
    client.setSessionLostHandler(() => {
      setUser(null);
      qc.clear(); // aucune donnée de la session précédente ne reste en mémoire
    });
    client
      .restoreSession()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, [qc]);

  const login = useCallback(async (u: string, p: string) => {
    const usr = await client.login(u, p);
    setUser(usr);
    return usr;
  }, []);

  const logout = useCallback(async () => {
    await client.logout();
    setUser(null);
    qc.clear();
  }, [qc]);

  const changePassword = useCallback(async (o: string, n: string) => {
    setUser(await client.changePassword(o, n));
  }, []);

  const value = useMemo(() => ({ user, ready, login, logout, changePassword }), [user, ready, login, logout, changePassword]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("AuthProvider manquant");
  return v;
}
