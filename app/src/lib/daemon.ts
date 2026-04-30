import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { invoke, listen } from "./ipc";

export type DaemonHealth = "healthy" | "degraded" | "stopped" | "starting";

export interface DaemonStatusPayload {
  status: DaemonHealth;
  port: number;
  attached: boolean;
  message?: string | null;
  restart_count: number;
  log_path: string;
}

export function useDaemonStatus() {
  const [status, setStatus] = useState<DaemonStatusPayload>({
    status: "starting",
    port: 19847,
    attached: false,
    message: null,
    restart_count: 0,
    log_path: "",
  });

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen<DaemonStatusPayload>("daemon://status", (event) => {
      setStatus(event.payload);
    }).then((fn) => {
      unlisten = fn;
    });

    invoke<DaemonStatusPayload>("daemon_status")
      .then(setStatus)
      .catch(() =>
        setStatus((current) => ({
          ...current,
          status: "stopped",
        })),
      );

    return () => unlisten?.();
  }, []);

  return status;
}

export interface SearchResult {
  id: string;
  similarity: number;
  file_path: string;
  file_name: string;
  media_category: string;
  timestamp: string;
  description: string;
  source: string;
  preview: string;
  metadata: Record<string, unknown>;
}

export function useSearch(query: string, sources?: string[]) {
  return useQuery<SearchResult[]>({
    queryKey: ["search", query, sources],
    queryFn: () =>
      invoke("search", {
        args: { query, n_results: 30, sources: sources?.length ? sources : null },
      }),
    enabled: query.trim().length > 0,
    staleTime: 10_000,
  });
}

export function useStats() {
  return useQuery({
    queryKey: ["stats"],
    queryFn: () => invoke<{ status: string; count: number }>("stats"),
    refetchInterval: 5_000,
  });
}

export function useSources() {
  return useQuery({
    queryKey: ["sources"],
    queryFn: () => invoke<string[]>("sources"),
    refetchInterval: 30_000,
  });
}

export interface ProgressInfo {
  indexing: boolean;
  queued: number;
  processed: number;
  embedded: number;
  skipped: number;
  errors: number;
  total_indexed: number;
}

export function useProgress() {
  return useQuery({
    queryKey: ["progress"],
    queryFn: () => invoke<ProgressInfo>("progress"),
    refetchInterval: 2_000,
  });
}

export interface IndexStatus {
  running: boolean;
  queued: number;
  processed: number;
  embedded: number;
  skipped: number;
  errors: number;
  active_path: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_error?: string | null;
}

export function useIndexStatus() {
  return useQuery({
    queryKey: ["index_status"],
    queryFn: () => invoke<IndexStatus>("index_status"),
    refetchInterval: 2_000,
  });
}

export interface ConnectorState {
  authenticated: boolean;
  last_sync: number;
  last_sync_iso: string | null;
  interval_s: number;
  last_result: Record<string, unknown>;
}

export function useConnectorStatus() {
  return useQuery({
    queryKey: ["connector_status"],
    queryFn: () => invoke<Record<string, ConnectorState>>("connector_status"),
    refetchInterval: 10_000,
  });
}

export function useWatchedDirs() {
  return useQuery({
    queryKey: ["watched_dirs"],
    queryFn: () => invoke<{ dirs: string[]; restart_required?: boolean }>("watched_dirs"),
  });
}

export function useWatchedDirsStats() {
  return useQuery({
    queryKey: ["watched_dirs_stats"],
    queryFn: () => invoke<{ stats: { path: string; count: number }[] }>("watched_dirs_stats"),
    refetchInterval: 15_000,
  });
}

export function useLogTail(lines = 200) {
  return useQuery({
    queryKey: ["log_tail", lines],
    queryFn: () => invoke<string>("read_log_tail", { lines }),
    refetchInterval: 2_000,
  });
}

export function useDaemonEnsureRunningMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => invoke<DaemonStatusPayload>("daemon_ensure_running"),
    onSettled: () => qc.invalidateQueries({ queryKey: ["stats"] }),
  });
}

export function useDaemonRestartMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => invoke<DaemonStatusPayload>("daemon_restart"),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["progress"] });
      qc.invalidateQueries({ queryKey: ["index_status"] });
      qc.invalidateQueries({ queryKey: ["connector_status"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useSyncMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (source?: string) => invoke("sync", { source: source ?? null }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["connector_status"] });
      qc.invalidateQueries({ queryKey: ["progress"] });
      qc.invalidateQueries({ queryKey: ["index_status"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useIndexWatchedDirsMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => invoke("index_watched_dirs"),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["progress"] });
      qc.invalidateQueries({ queryKey: ["index_status"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useAddWatchedDirMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => invoke("add_watched_dir", { path }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["watched_dirs"] });
      qc.invalidateQueries({ queryKey: ["watched_dirs_stats"] });
      qc.invalidateQueries({ queryKey: ["progress"] });
      qc.invalidateQueries({ queryKey: ["index_status"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export function useRemoveWatchedDirMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => invoke("remove_watched_dir", { path }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["watched_dirs"] });
      qc.invalidateQueries({ queryKey: ["watched_dirs_stats"] });
    },
  });
}

export function useDisconnectMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (source: string) => invoke("disconnect_source", { source }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["connector_status"] }),
  });
}

export function useConnectGoogleMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ source, scopes }: { source: string; scopes: string[] }) =>
      invoke<{ source: string; credentials_path: string }>("oauth_connect_google", {
        args: { source, scopes },
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["connector_status"] }),
  });
}
