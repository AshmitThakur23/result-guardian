import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  DischargeContractRequest,
  DischargeContractsCreated,
  DischargeOverrideRequest,
  DischargeOverrideResult,
  DischargeReadiness,
  DischargeResult,
  EncounterDetail,
  UserSummary,
} from "./types";

export const keys = {
  encounter: (id: string) => ["encounter", id] as const,
  readiness: (id: string) => ["encounter", id, "discharge-readiness"] as const,
  users: (role: string | undefined, q: string) => ["users", role ?? "*", q] as const,
};

export function useEncounter(encounterId: string) {
  return useQuery({
    queryKey: keys.encounter(encounterId),
    queryFn: () => api.get<EncounterDetail>(`/encounters/${encounterId}`),
    // Patient identity and the attending doctor do not change during a
    // discharge, unlike readiness.
    staleTime: 5 * 60 * 1000,
  });
}

export function useDischargeReadiness(encounterId: string) {
  return useQuery({
    queryKey: keys.readiness(encounterId),
    queryFn: () =>
      api.get<DischargeReadiness>(`/encounters/${encounterId}/discharge-readiness`),
  });
}

/**
 * Doctor search for the responsible-doctor field.
 *
 * `enabled` is not gated on a minimum query length: the field opens with the
 * first page of doctors already listed, so a doctor who does not know how a
 * colleague's name is spelled can still arrow through them.
 */
export function useDoctorSearch(query: string, role = "doctor") {
  return useQuery({
    queryKey: keys.users(role, query),
    queryFn: () => {
      const params = new URLSearchParams({ role, limit: "20" });
      if (query.trim()) params.set("q", query.trim());
      return api.get<UserSummary[]>(`/users?${params.toString()}`);
    },
    staleTime: 60 * 1000,
    placeholderData: (previous) => previous,
  });
}

/**
 * A name-lookup table for doctor ids the screen did not choose itself --
 * owners of pre-existing contracts, and the unit head an override flags to.
 *
 * `list_users` caps at 100 rows, so in a large hospital this will not contain
 * everyone. That is why callers fall back to a neutral phrase rather than
 * printing a raw uuid: a missing name is a display gap, never a wrong one.
 * A by-ids lookup belongs with the Phase 5.4 admin surface.
 */
export function useDoctorDirectory() {
  return useQuery({
    queryKey: ["users", "directory"] as const,
    queryFn: async () => {
      const users = await api.get<UserSummary[]>("/users?limit=100");
      return new Map(users.map((user) => [user.id, user]));
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useCreateContracts(encounterId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (contracts: DischargeContractRequest[]) =>
      api.post<DischargeContractsCreated>(
        `/encounters/${encounterId}/discharge-contracts`,
        { contracts },
      ),
    onSettled: () => {
      // Success or failure, readiness has to be re-read: a 409 means somebody
      // else contracted one of these orders, and the screen is now wrong.
      void queryClient.invalidateQueries({ queryKey: keys.readiness(encounterId) });
    },
  });
}

export function useDischarge(encounterId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    // No body by design -- the server re-derives readiness under a row lock
    // and ignores anything the client believes.
    mutationFn: () => api.post<DischargeResult>(`/encounters/${encounterId}/discharge`),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: keys.readiness(encounterId) });
      void queryClient.invalidateQueries({ queryKey: keys.encounter(encounterId) });
    },
  });
}

export function useDischargeOverride(encounterId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: DischargeOverrideRequest) =>
      api.post<DischargeOverrideResult>(
        `/encounters/${encounterId}/discharge-overrides`,
        payload,
      ),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: keys.readiness(encounterId) });
      void queryClient.invalidateQueries({ queryKey: keys.encounter(encounterId) });
    },
  });
}
