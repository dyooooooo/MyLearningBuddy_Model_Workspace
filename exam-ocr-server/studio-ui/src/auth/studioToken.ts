/** Browser Studio token for `/api/v1/studio/*` (gateway auth). */

const STORAGE_KEY = "studio.auth.token";

export function getStudioToken(): string {
  try {
    const fromQs = new URLSearchParams(window.location.search).get("token");
    if (fromQs && fromQs.trim()) {
      setStudioToken(fromQs.trim());
      const u = new URL(window.location.href);
      u.searchParams.delete("token");
      window.history.replaceState({}, "", u.pathname + u.search + u.hash);
      return fromQs.trim();
    }
  } catch {
    /* ignore */
  }
  try {
    const t = (localStorage.getItem(STORAGE_KEY) || "").trim();
    if (t) {
      // Keep cookie in sync for stylesheet/img requests.
      try {
        document.cookie = `studio_token=${encodeURIComponent(t)}; path=/; SameSite=Lax`;
      } catch {
        /* ignore */
      }
    }
    return t;
  } catch {
    return "";
  }
}

export function setStudioToken(token: string): void {
  const t = token.trim();
  try {
    localStorage.setItem(STORAGE_KEY, t);
  } catch {
    /* ignore */
  }
  // Same-origin cookie so <link>/<img> to /api/v1/studio/* pass auth.
  try {
    document.cookie = `studio_token=${encodeURIComponent(t)}; path=/; SameSite=Lax`;
  } catch {
    /* ignore */
  }
}

export function clearStudioToken(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  try {
    document.cookie = "studio_token=; path=/; Max-Age=0; SameSite=Lax";
  } catch {
    /* ignore */
  }
}

/** Blocking gate until a token is stored. Returns when ready. */
export function ensureStudioToken(): Promise<string> {
  const existing = getStudioToken();
  if (existing) return Promise.resolve(existing);

  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "studio-auth-gate";
    overlay.innerHTML = `
      <div class="studio-auth-gate__card">
        <h2>语料库工作台登录</h2>
        <p class="muted">请输入 Studio 令牌（与网关 <code>STUDIO_TOKEN</code> 一致）。</p>
        <input type="password" class="studio-auth-gate__input" autocomplete="current-password" placeholder="Studio token" />
        <p class="studio-auth-gate__err hidden"></p>
        <button type="button" class="btn-primary studio-auth-gate__ok">进入</button>
      </div>
    `;
    document.body.appendChild(overlay);
    const input = overlay.querySelector<HTMLInputElement>(".studio-auth-gate__input")!;
    const err = overlay.querySelector<HTMLElement>(".studio-auth-gate__err")!;
    const btn = overlay.querySelector<HTMLButtonElement>(".studio-auth-gate__ok")!;
    const submit = () => {
      const v = (input.value || "").trim();
      if (!v) {
        err.textContent = "令牌不能为空";
        err.classList.remove("hidden");
        return;
      }
      setStudioToken(v);
      overlay.remove();
      resolve(v);
    };
    btn.onclick = submit;
    input.onkeydown = (e) => {
      if (e.key === "Enter") submit();
    };
    input.focus();
  });
}

export function promptRelogin(message?: string): void {
  clearStudioToken();
  const msg = message || "登录已失效，请重新输入 Studio 令牌";
  window.alert(msg);
  void ensureStudioToken().then(() => window.location.reload());
}
