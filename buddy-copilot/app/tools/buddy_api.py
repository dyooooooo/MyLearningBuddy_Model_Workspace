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
        inherit_from_job_id: str = "",
    ) -> dict:
        body: dict[str, Any] = {
            "minio_key": minio_key,
            "edition_id": edition_id,
            "label": label,
            "subject": subject,
            "grade_band": grade_band,
        }
        if inherit_from_job_id:
            body["inherit_from_job_id"] = inherit_from_job_id
        if page_ranges:
            body["page_ranges"] = page_ranges
        elif page_limit:
            body["page_limit"] = page_limit
        return self._req("POST", "/api/v1/admin/corpus/import/pdf", json=body)

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

    def rebuild_ir(self, job_id: str, pages: list[int] | None = None) -> Any:
        """Layout-only IR rebuild（无 35B）；可指定页，合并更新 escalate 清单。"""
        body: dict[str, Any] = {}
        if pages:
            body["pages"] = [int(p) for p in pages]
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/import/jobs/{job_id}/rebuild-ir",
            json=body,
        )

    # ---- Improvement Points ----

    def ip_ingest_attempts(self, body: dict[str, Any]) -> dict:
        return self._req(
            "POST", "/api/v1/admin/corpus/improvement-points/ingest-attempts", json=body
        )

    def ip_confirm(self, body: dict[str, Any]) -> dict:
        return self._req("POST", "/api/v1/admin/corpus/improvement-points/confirm", json=body)

    def ip_converge(self, body: dict[str, Any]) -> dict:
        return self._req("POST", "/api/v1/admin/corpus/improvement-points/converge", json=body)

    def ip_list(
        self,
        *,
        fingerprint_hash: str = "",
        job_id: str = "",
        status: str = "",
        prep_status: str = "",
        limit: int = 0,
    ) -> dict:
        params: dict[str, Any] = {}
        if fingerprint_hash:
            params["fingerprint_hash"] = fingerprint_hash
        if job_id:
            params["job_id"] = job_id
        if status:
            params["status"] = status
        if prep_status:
            params["prep_status"] = prep_status
        if limit:
            params["limit"] = int(limit)
        return self._req("GET", "/api/v1/admin/corpus/improvement-points", params=params or None)

    def ip_resolve_conflict(self, ip_id: str, *, winner: bool = True) -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/improvement-points/{ip_id}/resolve-conflict",
            json={"winner": winner},
        )

    def ip_set_pattern(self, ip_id: str, body: dict[str, Any]) -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/improvement-points/{ip_id}/pattern",
            json=body,
        )

    def ip_list_for_triage(self, *, fingerprint_hash: str = "", limit: int = 2000) -> dict:
        params: dict[str, Any] = {"limit": int(limit or 2000)}
        if fingerprint_hash:
            params["fingerprint_hash"] = fingerprint_hash
        return self._req(
            "GET",
            "/api/v1/admin/corpus/improvement-points/list-for-triage",
            params=params,
        )

    def ip_list_for_split(self, *, fingerprint_hash: str = "", limit: int = 2000) -> dict:
        params: dict[str, Any] = {"limit": int(limit or 2000)}
        if fingerprint_hash:
            params["fingerprint_hash"] = fingerprint_hash
        return self._req(
            "GET",
            "/api/v1/admin/corpus/improvement-points/list-for-split",
            params=params,
        )

    def ip_conflict_hints(self, *, fingerprint_hash: str) -> dict:
        return self._req(
            "GET",
            "/api/v1/admin/corpus/improvement-points/conflict-hints",
            params={"fingerprint_hash": fingerprint_hash},
        )

    def ip_triage_apply(self, body: dict[str, Any]) -> dict:
        return self._req(
            "POST",
            "/api/v1/admin/corpus/improvement-points/triage-apply",
            json=body,
        )

    def ip_split_propose(
        self, ip_id: str, children: list[dict[str, Any]] | None = None
    ) -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/improvement-points/{ip_id}/split-propose",
            json={"children": list(children or [])},
        )

    def ip_split_apply(self, ip_id: str, children: list[dict[str, Any]]) -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/improvement-points/{ip_id}/split-apply",
            json={"children": list(children)},
        )

    def ip_conflict_resolve(
        self, *, keep_ids: list[str], delete_ids: list[str]
    ) -> dict:
        return self._req(
            "POST",
            "/api/v1/admin/corpus/improvement-points/conflict-resolve",
            json={"keep_ids": list(keep_ids), "delete_ids": list(delete_ids)},
        )

    # ---- 抽象轨（模式级） ----

    def patterns_list(
        self,
        *,
        category: str = "",
        status: str = "",
        landing_target: str = "",
        min_members: int = 0,
        with_members: bool = False,
        limit: int = 200,
    ) -> dict:
        params: dict[str, Any] = {"limit": int(limit)}
        if category:
            params["category"] = category
        if status:
            params["status"] = status
        if landing_target:
            params["landing_target"] = landing_target
        if min_members:
            params["min_members"] = int(min_members)
        if with_members:
            params["with_members"] = True
        return self._req("GET", "/api/v1/admin/corpus/patterns", params=params)

    def pattern_get(self, pattern_id: str) -> dict:
        return self._req("GET", f"/api/v1/admin/corpus/patterns/{pattern_id}")

    def pattern_route(self, pattern_id: str, landing_target: str, note: str = "") -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/patterns/{pattern_id}/route",
            json={"landing_target": landing_target, "note": note},
        )

    def pattern_land(self, pattern_id: str, landed_ref: str, note: str = "") -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/patterns/{pattern_id}/land",
            json={"landed_ref": landed_ref, "note": note},
        )

    def pattern_merge(self, pattern_id: str, merge_ids: list[str], note: str = "") -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/patterns/{pattern_id}/merge",
            json={"merge_ids": list(merge_ids), "note": note},
        )

    def pattern_split(
        self,
        pattern_id: str,
        member_ip_ids: list[str],
        abstract_text: str,
        *,
        category: str = "",
        note: str = "",
    ) -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/patterns/{pattern_id}/split",
            json={
                "member_ip_ids": list(member_ip_ids),
                "abstract_text": abstract_text,
                "category": category,
                "note": note,
            },
        )

    # ---- 症状检测器（抽象轨的自验闭环） ----

    def checks_list(self) -> dict:
        return self._req("GET", "/api/v1/admin/corpus/checks")

    def check_dry_run(
        self,
        job_id: str,
        *,
        suspect_type: str = "",
        yaml_text: str = "",
        pages: list[int] | None = None,
        limit: int = 40,
    ) -> dict:
        return self._req(
            "POST",
            "/api/v1/admin/corpus/checks/dry-run",
            json={
                "job_id": job_id,
                "suspect_type": suspect_type,
                "yaml_text": yaml_text,
                "pages": list(pages or []),
                "limit": int(limit),
            },
        )

    def check_run_examples(self, *, suspect_type: str = "", yaml_text: str = "") -> dict:
        return self._req(
            "POST",
            "/api/v1/admin/corpus/checks/run-examples",
            json={"suspect_type": suspect_type, "yaml_text": yaml_text},
        )

    def check_report_false_positive(
        self,
        suspect_type: str,
        *,
        fingerprint: str = "",
        job_id: str = "",
        page_no: int = 0,
        block_id: str = "",
        evidence: dict | None = None,
        note: str = "",
    ) -> dict:
        return self._req(
            "POST",
            "/api/v1/admin/corpus/checks/false-positive",
            json={
                "suspect_type": suspect_type,
                "fingerprint": fingerprint,
                "job_id": job_id,
                "page_no": int(page_no),
                "block_id": block_id,
                "evidence": evidence or {},
                "note": note,
            },
        )

    def pattern_bind_detector(
        self,
        pattern_id: str,
        suspect_type: str,
        *,
        detector_ref: str = "",
        remediation_kind: str = "",
        playbook_ref: str = "",
        landing_target: str = "",
    ) -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/patterns/{pattern_id}/bind-detector",
            json={
                "suspect_type": suspect_type,
                "detector_ref": detector_ref,
                "remediation_kind": remediation_kind,
                "playbook_ref": playbook_ref,
                "landing_target": landing_target,
            },
        )

    def pattern_promotion_check(self, pattern_id: str) -> dict:
        return self._req(
            "GET", f"/api/v1/admin/corpus/patterns/{pattern_id}/promotion-check"
        )

    def job_symptoms(self, job_id: str) -> dict:
        return self._req("GET", f"/api/v1/admin/corpus/jobs/{job_id}/symptoms")

    def refresh_job_symptoms(self, job_id: str) -> dict:
        return self._req(
            "POST", f"/api/v1/admin/corpus/jobs/{job_id}/symptoms/refresh"
        )

    def bind_book_edition(
        self,
        fingerprint_hash: str,
        edition_id: str,
        *,
        source_job_id: str = "",
        note: str = "",
    ) -> dict:
        return self._req(
            "POST",
            f"/api/v1/admin/corpus/books/{fingerprint_hash}/bind-edition",
            json={
                "edition_id": edition_id,
                "source_job_id": source_job_id,
                "note": note,
            },
        )

    def close(self) -> None:
        self._client.close()
