"""buddy-api 管理接口客户端（同步 httpx，跑在会话工作线程里）。

全部走 `/api/v1/admin/*`，Bearer ADMIN_TOKEN。
端点清单与 payload 形状以 learning-buddy-api 实际路由为准：
- editions:  app/routes/editions_admin.py
- corpus:    app/routes/corpus_admin.py
"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import Settings


class BuddyApiClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.buddy_api_base.rstrip("/")
        self._token = settings.resolve_admin_token()
        self._client = httpx.Client(
            base_url=self._base,
            timeout=httpx.Timeout(300.0, connect=10.0),
            headers={"Authorization": f"Bearer {self._token}"},
        )

    def _req(self, method: str, path: str, *, json: Any = None, params: dict | None = None) -> Any:
        r = self._client.request(method, path, json=json, params=params)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("detail", ""))[:500]
            except Exception:  # noqa: BLE001
                detail = r.text[:500]
            raise RuntimeError(f"buddy-api {method} {path} -> {r.status_code}: {detail}")
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text

    # ---- editions ----

    def editions_overview(self) -> dict:
        return self._req("GET", "/api/v1/admin/editions/overview")

    def list_editions(self) -> dict:
        return self._req("GET", "/api/v1/admin/editions")

    def get_bundle(self, edition_id: str, version: str | None = None) -> dict:
        params = {"version": version} if version else None
        return self._req("GET", f"/api/v1/admin/editions/{edition_id}/bundle", params=params)

    def get_draft(self, edition_id: str) -> dict:
        return self._req("GET", f"/api/v1/admin/editions/{edition_id}/draft")

    def save_draft(self, edition_id: str, bundle: dict, updated_by: str = "buddy-copilot") -> dict:
        return self._req(
            "PUT",
            f"/api/v1/admin/editions/{edition_id}/draft",
            json={"bundle": bundle, "updated_by": updated_by},
        )

    def fork_edition(self, source_id: str, new_id: str, display_name: str = "") -> dict:
        return self._req(
            "POST",
            "/api/v1/admin/editions/fork",
            json={"source_id": source_id, "new_id": new_id, "display_name": display_name},
        )

    def publish_edition(self, edition_id: str, note: str = "", published_by: str = "buddy-copilot") -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/editions/{edition_id}/publish",
            json={"note": note, "published_by": published_by},
        )

    def calibrate(
        self,
        edition_id: str,
        sample_bboxes: list[list[float]] | None = None,
        display_name: str = "",
        persist_fork: bool = False,
    ) -> dict:
        return self._req(
            "POST",
            "/api/v1/admin/editions/calibrate",
            json={
                "edition_id": edition_id,
                "display_name": display_name,
                "sample_bboxes": sample_bboxes,
                "persist_fork": persist_fork,
            },
        )

    def run_golden(self, edition_id: str) -> dict:
        return self._req("POST", f"/api/v1/admin/editions/{edition_id}/golden")

    # ---- corpus / import ----

    def list_import_jobs(self) -> Any:
        return self._req("GET", "/api/v1/admin/corpus/import/jobs")

    def get_import_job(self, job_id: str) -> dict:
        return self._req("GET", f"/api/v1/admin/corpus/import/jobs/{job_id}")

    def start_pdf_import(
        self,
        minio_key: str,
        edition_id: str,
        label: str = "",
        subject: str = "math",
        grade_band: str = "hs",
        page_limit: int | None = None,
        page_ranges: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "minio_key": minio_key,
            "edition_id": edition_id,
            "label": label,
            "subject": subject,
            "grade_band": grade_band,
        }
        if page_ranges:
            body["page_ranges"] = page_ranges
        elif page_limit:
            body["page_limit"] = page_limit
        return self._req("POST", "/api/v1/admin/corpus/import/pdf", json=body)

    def extend_pdf_import(self, job_id: str, page_ranges: str) -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/import/jobs/{job_id}/extend",
            json={"page_ranges": page_ranges},
        )

    def enqueue_correction(
        self,
        edition_id: str,
        candidate: dict[str, Any],
        source: str = "ai_import",
        source_ref: str = "",
    ) -> dict:
        return self._req(
            "POST",
            "/api/v1/admin/editions/corrections/enqueue",
            json={
                "edition_id": edition_id,
                "candidate": candidate,
                "source": source,
                "source_ref": source_ref,
            },
        )

    def get_job_blocks(self, job_id: str) -> Any:
        return self._req("GET", f"/api/v1/admin/corpus/import/jobs/{job_id}/blocks")

    def patch_job_block(self, job_id: str, block_id: str, patch: dict) -> Any:
        return self._req(
            "PATCH",
            f"/api/v1/admin/corpus/import/jobs/{job_id}/blocks/{block_id}",
            json=patch,
        )

    def replace_page_blocks(
        self,
        job_id: str,
        page_no: int,
        blocks: list[dict],
        *,
        rerender: bool = True,
    ) -> Any:
        return self._req(
            "PUT",
            f"/api/v1/admin/corpus/import/jobs/{job_id}/pages/{page_no}/blocks",
            json={"blocks": blocks, "rerender": rerender},
        )

    def rerender_page(self, job_id: str, page_no: int) -> Any:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/import/jobs/{job_id}/rerender-page/{page_no}",
        )

    def get_page_html(self, job_id: str, page_no: int) -> Any:
        return self._req("GET", f"/api/v1/admin/corpus/import/jobs/{job_id}/page/{page_no}/html")

    def get_rerecognize_context(self, job_id: str, page_no: int, block_id: str | None = None) -> dict:
        params = {"block_id": block_id} if block_id else None
        return self._req(
            "GET",
            f"/api/v1/admin/corpus/import/jobs/{job_id}/pages/{page_no}/rerecognize-context",
            params=params,
        )

    def reprocess_page(self, job_id: str, page_no: int, mode: str, user_note: str = "") -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/import/jobs/{job_id}/pages/{page_no}/reprocess",
            json={"mode": mode, "user_note": user_note},
        )

    def close(self) -> None:
        self._client.close()
