"use client";

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
