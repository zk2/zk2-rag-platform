"use client";

import { useEffect, useState } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";

type Membership = {
  org_id: number;
  org_slug: string;
  org_name: string;
  role: string;
};

type User = {
  id: number;
  email: string;
  full_name: string | null;
  is_super_admin: boolean;
};

type State = {
  accessToken: string | null;
  refreshToken: string | null;
  user: User | null;
  memberships: Membership[];
  currentOrgId: number | null;
};

type Actions = {
  setTokens: (accessToken: string, refreshToken: string) => void;
  setMe: (user: User, memberships: Membership[]) => void;
  setCurrentOrg: (orgId: number | null) => void;
  clear: () => void;
};

export const useAuthStore = create<State & Actions>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      memberships: [],
      currentOrgId: null,
      setTokens: (accessToken, refreshToken) => set({ accessToken, refreshToken }),
      setMe: (user, memberships) =>
        set((s) => ({
          user,
          memberships,
          currentOrgId: s.currentOrgId ?? memberships[0]?.org_id ?? null,
        })),
      setCurrentOrg: (currentOrgId) => set({ currentOrgId }),
      clear: () =>
        set({
          accessToken: null,
          refreshToken: null,
          user: null,
          memberships: [],
          currentOrgId: null,
        }),
    }),
    { name: "zk2-auth" },
  ),
);

/**
 * Whether the persisted store has been read back from localStorage yet.
 *
 * On a full page load zustand hydrates asynchronously, so the first render
 * always sees a logged-out state. Anything that redirects on "no user" has to
 * wait for this, or a signed-in visitor is bounced to /login every time they
 * reload a page.
 */
export function useAuthHydrated(): boolean {
  // The persist API is absent while Next prerenders on the server, where there
  // is no storage to hydrate from - reading it unguarded fails the build
  const [hydrated, setHydrated] = useState(() => useAuthStore.persist?.hasHydrated() ?? false);

  useEffect(() => {
    const store = useAuthStore.persist;
    if (!store) return;
    const unsubscribe = store.onFinishHydration(() => setHydrated(true));
    setHydrated(store.hasHydrated());
    return unsubscribe;
  }, []);

  return hydrated;
}
