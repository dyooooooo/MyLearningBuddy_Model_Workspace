/** Client helpers — server-side normalize (full JPEG, not thumbnail). */

const IMAGE_EXTENSIONS = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".heic",
  ".heif",
  ".webp",
  ".gif",
  ".bmp",
  ".tif",
  ".tiff",
]);

export function isImageFile(file) {
  if (!file) return false;
  if (file.type?.startsWith("image/")) return true;
  const name = file.name ?? "";
  const dot = name.lastIndexOf(".");
  if (dot < 0) return false;
  return IMAGE_EXTENSIONS.has(name.slice(dot).toLowerCase());
}

/**
 * Upload file to gateway normalize endpoint; returns full-resolution JPEG blob + object URL.
 */
export async function normalizeImageFile(file) {
  const form = new FormData();
  form.append("file", file, file.name || "upload");
  const res = await fetch("/api/v1/image/normalize", { method: "POST", body: form });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      detail = err.detail ?? JSON.stringify(err);
      if (Array.isArray(detail)) {
        detail = detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");
      }
    } catch {
      detail = (await res.text()) || detail;
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  return {
    blob,
    url,
    sourceFormat: res.headers.get("X-Source-Format"),
    width: Number(res.headers.get("X-Image-Width") || 0),
    height: Number(res.headers.get("X-Image-Height") || 0),
  };
}
