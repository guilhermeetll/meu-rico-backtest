import type { BacktestResult, HistoryItem, StudyResult } from "./types";

async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => null);
  if (body && typeof body.detail === "string") return body.detail;
  if (body && Array.isArray(body.detail)) {
    return body.detail.map((item: { msg?: string }) => item.msg ?? "pedido inválido").join(" ");
  }
  return `A API respondeu ${response.status}.`;
}

export async function runBacktest(payload: unknown): Promise<BacktestResult> {
  const response = await fetch("/api/backtests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function listBacktests(): Promise<HistoryItem[]> {
  const response = await fetch("/api/backtests");
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function getBacktest(id: string): Promise<BacktestResult> {
  const response = await fetch(`/api/backtests/${id}`);
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function runStudy(payload: unknown): Promise<StudyResult> {
  const response = await fetch("/api/studies", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function uploadCsv(file: File): Promise<string> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/uploads", { method: "POST", body });
  if (!response.ok) throw new Error(await errorMessage(response));
  const payload = (await response.json()) as { id: string };
  return payload.id;
}
