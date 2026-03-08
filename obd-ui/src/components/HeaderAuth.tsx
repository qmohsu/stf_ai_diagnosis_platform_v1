"use client";

import { useAuth } from "./AuthProvider";

export function HeaderAuth() {
  const { username, logout, isLoading } = useAuth();

  if (isLoading || !username) return null;

  return (
    <div className="ml-auto flex items-center gap-3">
      <span className="text-sm text-muted-foreground">{username}</span>
      <button
        onClick={logout}
        className="rounded border px-2 py-1 text-xs hover:bg-muted"
      >
        Logout
      </button>
    </div>
  );
}
