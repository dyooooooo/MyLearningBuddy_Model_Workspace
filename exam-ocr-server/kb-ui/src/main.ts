import { ensureStudioToken } from "./auth/studioToken";
import { mountApp } from "./app";
import "./styles.css";

async function boot(): Promise<void> {
  await ensureStudioToken();
  const root = document.getElementById("app");
  if (root) mountApp(root);
}

void boot();
