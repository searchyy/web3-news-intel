import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { AuthMe } from "../types/api";

type AuthContextValue = {
  user: AuthMe | null;
  csrf: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  setUser: (user: AuthMe | null) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthMe | null>(null);
  const [loading, setLoading] = useState(true);
  const csrf = user?.csrf_token ?? null;

  useEffect(() => {
    let active = true;
    api<AuthMe>("/api/admin/auth/me", { authRedirect: false })
      .then((result) => {
        if (active && result.authenticated) {
          setUser(result);
        }
      })
      .catch(() => {
        if (active) {
          setUser(null);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      csrf,
      loading,
      setUser,
      async login(username, password) {
        const result = await api<AuthMe>("/api/admin/auth/login", {
          method: "POST",
          body: JSON.stringify({ username, password })
        });
        setUser(result);
      },
      async logout() {
        await api<AuthMe>("/api/admin/auth/logout", { method: "POST", csrf });
        setUser(null);
      }
    }),
    [user, csrf, loading]
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("AuthProvider is missing");
  }
  return context;
}
