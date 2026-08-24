from __future__ import annotations

from sqlalchemy import Engine, text

from app.config import settings


def apply_pilot_migrations(engine: Engine) -> None:
    """Add M2 columns/tables on existing PostgreSQL pilot DB (create_all is non-destructive)."""
    if not settings.use_postgres:
        return
    statements = [
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS tier VARCHAR(32) DEFAULT 'trial'",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS trial_started_at VARCHAR(64) DEFAULT ''",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS trial_expires_at VARCHAR(64) DEFAULT ''",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS org_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE question_entities ADD COLUMN IF NOT EXISTS eval_confidence DOUBLE PRECISION",
        "ALTER TABLE question_entities ADD COLUMN IF NOT EXISTS eval_json TEXT DEFAULT '{}'",
        "ALTER TABLE question_entities ADD COLUMN IF NOT EXISTS dwell_ms INTEGER",
        "ALTER TABLE question_entities ADD COLUMN IF NOT EXISTS print_segments_json TEXT DEFAULT '[]'",
        "ALTER TABLE question_entities ADD COLUMN IF NOT EXISTS handwriting_segments_json TEXT DEFAULT '[]'",
        "ALTER TABLE mastery_views ADD COLUMN IF NOT EXISTS fluency DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE mastery_views ADD COLUMN IF NOT EXISTS fluency_ratio DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE mastery_views ADD COLUMN IF NOT EXISTS wrong_rate DOUBLE PRECISION DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS corpus_import_jobs (
            id VARCHAR(64) PRIMARY KEY,
            status VARCHAR(32) DEFAULT 'uploaded',
            source_channel VARCHAR(32) DEFAULT 'pdf_import',
            minio_key VARCHAR(512) DEFAULT '',
            markdown TEXT DEFAULT '',
            textbook_node_id VARCHAR(256) DEFAULT '',
            subject VARCHAR(32) DEFAULT 'math',
            grade_band VARCHAR(16) DEFAULT 'hs',
            label VARCHAR(512) DEFAULT '',
            error TEXT DEFAULT '',
            meta_json TEXT DEFAULT '{}',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS corpus_scout_runs (
            id VARCHAR(64) PRIMARY KEY,
            query TEXT DEFAULT '',
            url TEXT DEFAULT '',
            subject VARCHAR(32) DEFAULT 'math',
            grade_band VARCHAR(16) DEFAULT 'hs',
            status VARCHAR(32) DEFAULT 'running',
            steps_json TEXT DEFAULT '[]',
            inbox_count INTEGER DEFAULT 0,
            started_at VARCHAR(64) DEFAULT '',
            finished_at VARCHAR(64) DEFAULT '',
            error TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS import35b_feedback (
            id VARCHAR(64) PRIMARY KEY,
            job_id VARCHAR(64) DEFAULT '',
            page_no INTEGER DEFAULT 0,
            textbook_key VARCHAR(256) DEFAULT '',
            label VARCHAR(512) DEFAULT '',
            body TEXT DEFAULT '',
            tags_json TEXT DEFAULT '[]',
            block_id VARCHAR(128) DEFAULT '',
            screenshot_key VARCHAR(512) DEFAULT '',
            input_page_key VARCHAR(512) DEFAULT '',
            t1_snapshot_json TEXT DEFAULT '[]',
            debug_prefix VARCHAR(512) DEFAULT '',
            prompt_version VARCHAR(64) DEFAULT '',
            status VARCHAR(32) DEFAULT 'open',
            pack_path VARCHAR(512) DEFAULT '',
            resolution_note TEXT DEFAULT '',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_import35b_feedback_job ON import35b_feedback (job_id, status)",
        """CREATE TABLE IF NOT EXISTS corpus_import_feedback (
            id VARCHAR(64) PRIMARY KEY,
            job_id VARCHAR(64) DEFAULT '',
            page_no INTEGER DEFAULT 0,
            label VARCHAR(512) DEFAULT '',
            body TEXT DEFAULT '',
            tags_json TEXT DEFAULT '[]',
            block_id VARCHAR(128) DEFAULT '',
            screenshot_key VARCHAR(512) DEFAULT '',
            input_page_key VARCHAR(512) DEFAULT '',
            t1_snapshot_json TEXT DEFAULT '[]',
            debug_prefix VARCHAR(512) DEFAULT '',
            status VARCHAR(32) DEFAULT 'open',
            pack_path VARCHAR(512) DEFAULT '',
            resolution_note TEXT DEFAULT '',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_corpus_import_feedback_job ON corpus_import_feedback (job_id, status)",
        """CREATE TABLE IF NOT EXISTS corpus_book_fingerprints (
            id VARCHAR(64) PRIMARY KEY,
            fingerprint_hash VARCHAR(64) DEFAULT '',
            subject VARCHAR(32) DEFAULT 'math',
            grade_band VARCHAR(16) DEFAULT 'hs',
            page_count INTEGER DEFAULT 0,
            toc_hash VARCHAR(64) DEFAULT '',
            first_page_hash VARCHAR(64) DEFAULT '',
            pdf_bytes_size INTEGER DEFAULT 0,
            label_hint VARCHAR(512) DEFAULT '',
            features_json TEXT DEFAULT '{}',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_corpus_book_fp_hash ON corpus_book_fingerprints (fingerprint_hash)",
        """CREATE TABLE IF NOT EXISTS corpus_corrections (
            id VARCHAR(64) PRIMARY KEY,
            fingerprint_id VARCHAR(64) DEFAULT '',
            fingerprint_hash VARCHAR(64) DEFAULT '',
            scope VARCHAR(16) DEFAULT 'page',
            page_no INTEGER DEFAULT 0,
            block_id VARCHAR(128) DEFAULT '',
            user_note TEXT DEFAULT '',
            before_json TEXT DEFAULT '[]',
            after_json TEXT DEFAULT '[]',
            refine_result_json TEXT DEFAULT '{}',
            source_job_id VARCHAR(64) DEFAULT '',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_corpus_corr_fp_page ON corpus_corrections (fingerprint_hash, page_no)",
        "ALTER TABLE corpus_import_jobs ADD COLUMN IF NOT EXISTS edition_id VARCHAR(64) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_corpus_import_jobs_edition ON corpus_import_jobs (edition_id)",
        """CREATE TABLE IF NOT EXISTS editions (
            id VARCHAR(64) PRIMARY KEY,
            display_name VARCHAR(256) DEFAULT '',
            base_id VARCHAR(64) DEFAULT '_base',
            subject VARCHAR(32) DEFAULT 'math',
            grade_band VARCHAR(16) DEFAULT 'hs',
            description TEXT DEFAULT '',
            published_version VARCHAR(32) DEFAULT 'seed',
            status VARCHAR(32) DEFAULT 'active',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS edition_versions (
            id VARCHAR(64) PRIMARY KEY,
            edition_id VARCHAR(64) DEFAULT '',
            version VARCHAR(32) DEFAULT '',
            bundle_json TEXT DEFAULT '{}',
            note TEXT DEFAULT '',
            published_by VARCHAR(128) DEFAULT '',
            created_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_edition_versions_eid ON edition_versions (edition_id, version)",
        """CREATE TABLE IF NOT EXISTS edition_drafts (
            edition_id VARCHAR(64) PRIMARY KEY,
            bundle_json TEXT DEFAULT '{}',
            base_version VARCHAR(32) DEFAULT 'seed',
            updated_by VARCHAR(128) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS edition_correction_queue (
            id VARCHAR(64) PRIMARY KEY,
            edition_id VARCHAR(64) DEFAULT '',
            source VARCHAR(32) DEFAULT 'feedback',
            source_ref VARCHAR(128) DEFAULT '',
            candidate_json TEXT DEFAULT '{}',
            status VARCHAR(32) DEFAULT 'pending',
            reviewer VARCHAR(128) DEFAULT '',
            review_note TEXT DEFAULT '',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_edition_corr_status ON edition_correction_queue (edition_id, status)",
        "ALTER TABLE corpus_corrections ADD COLUMN IF NOT EXISTS ip_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE corpus_corrections ADD COLUMN IF NOT EXISTS procedure_json TEXT DEFAULT '{}'",
        "CREATE INDEX IF NOT EXISTS ix_corpus_corr_ip ON corpus_corrections (ip_id)",
        """CREATE TABLE IF NOT EXISTS corpus_improvement_points (
            id VARCHAR(64) PRIMARY KEY,
            fingerprint_hash VARCHAR(64) DEFAULT '',
            source_job_id VARCHAR(64) DEFAULT '',
            source_session_id VARCHAR(64) DEFAULT '',
            locus_key VARCHAR(256) DEFAULT '',
            page_no INTEGER DEFAULT 0,
            category VARCHAR(64) DEFAULT '',
            authority VARCHAR(32) DEFAULT 'edition',
            status VARCHAR(32) DEFAULT 'observed',
            claim_json TEXT DEFAULT '{}',
            evidence_json TEXT DEFAULT '{}',
            procedure_json TEXT DEFAULT '{}',
            concrete_json TEXT DEFAULT '{}',
            pattern_id VARCHAR(64) DEFAULT '',
            pattern_status VARCHAR(32) DEFAULT 'none',
            superseded_by VARCHAR(64) DEFAULT '',
            merged_into_id VARCHAR(64) DEFAULT '',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_corpus_ip_fp_status ON corpus_improvement_points (fingerprint_hash, status)",
        "CREATE INDEX IF NOT EXISTS ix_corpus_ip_job ON corpus_improvement_points (source_job_id)",
        "CREATE INDEX IF NOT EXISTS ix_corpus_ip_locus ON corpus_improvement_points (locus_key)",
        "ALTER TABLE corpus_improvement_points ADD COLUMN IF NOT EXISTS origin VARCHAR(32) DEFAULT ''",
        "ALTER TABLE corpus_improvement_points ADD COLUMN IF NOT EXISTS summary TEXT DEFAULT ''",
        "ALTER TABLE corpus_improvement_points ADD COLUMN IF NOT EXISTS kind VARCHAR(64) DEFAULT ''",
        "ALTER TABLE corpus_improvement_points ADD COLUMN IF NOT EXISTS needs_split INTEGER DEFAULT 0",
        "ALTER TABLE corpus_improvement_points ADD COLUMN IF NOT EXISTS prep_status VARCHAR(32) DEFAULT 'needs_triage'",
        "CREATE INDEX IF NOT EXISTS ix_corpus_ip_fp_prep ON corpus_improvement_points (fingerprint_hash, prep_status)",
        "CREATE INDEX IF NOT EXISTS ix_corpus_ip_origin ON corpus_improvement_points (origin)",
        """CREATE TABLE IF NOT EXISTS schema_meta (
            key VARCHAR(128) PRIMARY KEY,
            value TEXT DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS corpus_improvement_links (
            id VARCHAR(64) PRIMARY KEY,
            from_id VARCHAR(64) DEFAULT '',
            to_id VARCHAR(64) DEFAULT '',
            rel VARCHAR(32) DEFAULT '',
            created_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_corpus_ip_link_from ON corpus_improvement_links (from_id)",
        """CREATE TABLE IF NOT EXISTS corpus_improvement_patterns (
            id VARCHAR(64) PRIMARY KEY,
            category VARCHAR(64) DEFAULT '',
            abstract_text TEXT DEFAULT '',
            procedure_template_json TEXT DEFAULT '{}',
            target_layer VARCHAR(8) DEFAULT 'L1',
            edition_family_id VARCHAR(64) DEFAULT '',
            status VARCHAR(32) DEFAULT 'draft',
            member_count INTEGER DEFAULT 0,
            landed_ref VARCHAR(512) DEFAULT '',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_corpus_ip_pat_cat ON corpus_improvement_patterns (category, status)",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS landing_target VARCHAR(32) DEFAULT ''",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS landing_note TEXT DEFAULT ''",
        # 映射面：让模式能在新书里被检测器认出来
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS suspect_type VARCHAR(64) DEFAULT ''",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS detector_ref VARCHAR(256) DEFAULT ''",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS remediation_kind VARCHAR(16) DEFAULT ''",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS playbook_ref TEXT DEFAULT ''",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS fingerprints_json TEXT DEFAULT '[]'",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS hits INTEGER DEFAULT 0",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS true_positive INTEGER DEFAULT 0",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS false_positive INTEGER DEFAULT 0",
        "ALTER TABLE corpus_improvement_patterns ADD COLUMN IF NOT EXISTS book_count INTEGER DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS ix_corpus_ip_pat_suspect ON corpus_improvement_patterns (suspect_type)",
        """CREATE TABLE IF NOT EXISTS corpus_symptom_false_positives (
            id VARCHAR(64) PRIMARY KEY,
            suspect_type VARCHAR(64) DEFAULT '',
            fingerprint VARCHAR(64) DEFAULT '',
            pattern_id VARCHAR(64) DEFAULT '',
            job_id VARCHAR(64) DEFAULT '',
            fingerprint_hash VARCHAR(64) DEFAULT '',
            page_no INTEGER DEFAULT 0,
            block_id VARCHAR(64) DEFAULT '',
            evidence_json TEXT DEFAULT '{}',
            note TEXT DEFAULT '',
            created_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_corpus_symptom_fp_fingerprint ON corpus_symptom_false_positives (fingerprint)",
        "CREATE INDEX IF NOT EXISTS ix_corpus_symptom_fp_suspect ON corpus_symptom_false_positives (suspect_type)",
        """CREATE TABLE IF NOT EXISTS corpus_book_edition_bind (
            fingerprint_hash VARCHAR(64) PRIMARY KEY,
            edition_id VARCHAR(64) DEFAULT '',
            source_job_id VARCHAR(64) DEFAULT '',
            note TEXT DEFAULT '',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_corpus_book_ed_bind_eid ON corpus_book_edition_bind (edition_id)",
        """CREATE TABLE IF NOT EXISTS corpus_inbox_items (
            id VARCHAR(64) PRIMARY KEY,
            scout_run_id VARCHAR(64) DEFAULT '',
            status VARCHAR(32) DEFAULT 'pending',
            source_url TEXT DEFAULT '',
            source_platform VARCHAR(64) DEFAULT '',
            resource_kind VARCHAR(32) DEFAULT 'article',
            subject VARCHAR(32) DEFAULT 'math',
            grade_band VARCHAR(16) DEFAULT 'hs',
            title VARCHAR(512) DEFAULT '',
            snippet TEXT DEFAULT '',
            proposed_node_ids_json TEXT DEFAULT '[]',
            agent_confidence DOUBLE PRECISION DEFAULT 0,
            agent_notes_json TEXT DEFAULT '{}',
            reviewer VARCHAR(128) DEFAULT '',
            reviewed_at VARCHAR(64) DEFAULT '',
            created_at VARCHAR(64) DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS kb_books (
            id VARCHAR(64) PRIMARY KEY,
            label VARCHAR(512) DEFAULT '',
            filename VARCHAR(512) DEFAULT '',
            subject VARCHAR(32) DEFAULT 'math',
            grade_band VARCHAR(16) DEFAULT 'hs',
            textbook_hint VARCHAR(512) DEFAULT '',
            minio_key VARCHAR(512) DEFAULT '',
            status VARCHAR(32) DEFAULT 'uploaded',
            page_count INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_kb_books_status ON kb_books (status)",
        "ALTER TABLE kb_books ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE",
        "ALTER TABLE kb_books ADD COLUMN IF NOT EXISTS toc_json TEXT DEFAULT '{}'",
        """CREATE TABLE IF NOT EXISTS kb_pages (
            book_id VARCHAR(64) NOT NULL,
            page_no INTEGER NOT NULL,
            markdown TEXT DEFAULT '',
            image_key VARCHAR(512) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT '',
            PRIMARY KEY (book_id, page_no)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_kb_page_book ON kb_pages (book_id)",
        """CREATE TABLE IF NOT EXISTS textbook_eval_requests (
            request_id VARCHAR(128) PRIMARY KEY,
            question_id VARCHAR(64) DEFAULT '',
            homework_id VARCHAR(64) DEFAULT '',
            student_id VARCHAR(64) DEFAULT '',
            status VARCHAR(32) DEFAULT 'pending',
            created_at VARCHAR(64) DEFAULT '',
            updated_at VARCHAR(64) DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS ix_textbook_eval_question ON textbook_eval_requests (question_id)",
        "CREATE INDEX IF NOT EXISTS ix_textbook_eval_status ON textbook_eval_requests (status)",
    ]
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                  IF to_regclass('public.grok_eval_requests') IS NOT NULL THEN
                    INSERT INTO textbook_eval_requests
                      (request_id, question_id, homework_id, student_id, status, created_at, updated_at)
                    SELECT request_id, question_id, homework_id, student_id, status, created_at, updated_at
                    FROM grok_eval_requests
                    ON CONFLICT (request_id) DO NOTHING;
                  END IF;
                END $$;
                """
            )
        )
        # 一次性：全部打回待整理（Cursor Agent「整理」再分类）；schema_meta 防重复
        meta = conn.execute(
            text("SELECT value FROM schema_meta WHERE key = 'ip_prep_triage_v2'")
        ).fetchone()
        if meta is None:
            conn.execute(
                text(
                    """
                    UPDATE corpus_improvement_points
                    SET prep_status = 'needs_triage', needs_split = 0
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO schema_meta (key, value, updated_at)
                    VALUES ('ip_prep_triage_v2', '1', CAST(CURRENT_TIMESTAMP AS VARCHAR))
                    """
                )
            )
        else:
            # 兼容旧码 needs_conflict_resolve → needs_aggregate
            conn.execute(
                text(
                    """
                    UPDATE corpus_improvement_points
                    SET prep_status = 'needs_aggregate'
                    WHERE prep_status = 'needs_conflict_resolve'
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE corpus_improvement_points
                    SET prep_status = 'needs_triage'
                    WHERE prep_status IS NULL OR prep_status = ''
                    """
                )
            )
