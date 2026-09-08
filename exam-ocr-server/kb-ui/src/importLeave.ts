/** Leave-import must not kill a job already waiting on the shared Cursor bridge. */

export function shouldAbandonIdentify(state: {
  committed: boolean;
  identifyJobId: string;
  chatStarted?: boolean;
}): boolean {
  return !state.committed && !String(state.identifyJobId || "").trim() && !state.chatStarted;
}
