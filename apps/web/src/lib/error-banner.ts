"use client";

import { create } from "zustand";

/**
 * The last failure a background query hit.
 *
 * Mutations report their own errors next to the button that caused them, but a
 * list query that fails has nowhere to say so - the page just renders empty,
 * which reads as "there is nothing here" rather than "this did not load". The
 * query cache pushes those failures here and the app shell shows them.
 */
type State = {
  message: string | null;
  report: (message: string) => void;
  dismiss: () => void;
};

export const useErrorBanner = create<State>()((set) => ({
  message: null,
  report: (message) => set({ message }),
  dismiss: () => set({ message: null }),
}));
