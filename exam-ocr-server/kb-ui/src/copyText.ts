/** Copy text on LAN HTTP as well as localhost.

The Clipboard API needs a secure context (HTTPS or 127.0.0.1). The knowledge
base is opened as ``http://192.168.1.10:8000/kb/``, so ``writeText`` throws
and the old click handler swallowed it. ``document.execCommand("copy")`` still
works from a real click on that origin.
*/
export async function copyText(text: string): Promise<boolean> {
  const value = String(text ?? "");
  if (!value) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* fall through to execCommand */
  }
  return copyViaExec(value);
}

function copyViaExec(value: string): boolean {
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.left = "-9999px";
  area.style.top = "0";
  document.body.appendChild(area);
  area.focus();
  area.select();
  area.setSelectionRange(0, area.value.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}
