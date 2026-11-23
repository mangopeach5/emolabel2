"""SQLite schema and persistence for registration, review, and export."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from emolabel2.domain import CandidateRecord, PredictionRecord, VideoRecord
from emolabel2.intake.hashing import sha256_file
from emolabel2.intake.manifest import ManifestRow
from emolabel2.paths import resolve_manifest_input


SCHEMA_VERSION = 11


def open_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS schema_info (version INTEGER NOT NULL)")
    version_row = connection.execute("SELECT version FROM schema_info").fetchone()
    existing_version = version_row["version"] if version_row is not None else None
    if existing_version not in (None, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, SCHEMA_VERSION):
        raise RuntimeError(f"unsupported schema version: {existing_version}")

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            local_path TEXT NOT NULL UNIQUE,
            file_hash TEXT NOT NULL UNIQUE CHECK(
                length(file_hash) = 64 AND file_hash NOT GLOB '*[^0-9a-f]*'
            ),
            source_name TEXT NOT NULL,
            rights_status TEXT NOT NULL CHECK(rights_status = 'approved_research'),
            rights_basis TEXT NOT NULL CHECK(length(trim(rights_basis)) > 0),
            allowed_use TEXT NOT NULL CHECK(length(trim(allowed_use)) > 0),
            duration_s REAL NOT NULL CHECK(duration_s > 0),
            added_at TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL REFERENCES videos(video_id),
            start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
            end_ms INTEGER NOT NULL CHECK(end_ms > start_ms),
            status TEXT NOT NULL CHECK(status IN ('generated', 'prelabelled', 'reviewed')),
            transcript TEXT NOT NULL DEFAULT '',
            algorithm_version TEXT NOT NULL DEFAULT '',
            clip_path TEXT,
            file_hash TEXT CHECK(
                file_hash IS NULL OR (
                    length(file_hash) = 64 AND file_hash NOT GLOB '*[^0-9a-f]*'
                )
            )
        );

        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
            emotion TEXT CHECK(emotion IN ('neutral', 'angry', 'sad', 'happy') OR emotion IS NULL),
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            elapsed_s REAL NOT NULL CHECK(elapsed_s >= 0),
            needs_review INTEGER NOT NULL CHECK(needs_review IN (0, 1)),
            CHECK(emotion IS NOT NULL OR needs_review = 1)
        );

        CREATE TABLE IF NOT EXISTS reviews (
            candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id),
            review_status TEXT NOT NULL CHECK(review_status IN ('pending', 'accepted', 'rejected', 'deferred')),
            gold_emotion TEXT CHECK(gold_emotion IN ('neutral', 'angry', 'sad', 'happy') OR gold_emotion IS NULL),
            human_time_s REAL NOT NULL CHECK(human_time_s >= 0),
            reviewed_at TEXT NOT NULL,
            reject_reason TEXT NOT NULL DEFAULT '',
            CHECK(
                (review_status = 'accepted' AND gold_emotion IS NOT NULL)
                OR (review_status != 'accepted' AND gold_emotion IS NULL)
            ),
            CHECK(review_status != 'rejected' OR length(trim(reject_reason)) > 0)
        );

        CREATE TABLE IF NOT EXISTS review_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
            previous_status TEXT CHECK(
                previous_status IN ('pending', 'accepted', 'rejected', 'deferred')
                OR previous_status IS NULL
            ),
            new_status TEXT NOT NULL CHECK(
                new_status IN ('pending', 'accepted', 'rejected', 'deferred')
            ),
            previous_gold_emotion TEXT CHECK(
                previous_gold_emotion IN ('neutral', 'angry', 'sad', 'happy')
                OR previous_gold_emotion IS NULL
            ),
            new_gold_emotion TEXT CHECK(
                new_gold_emotion IN ('neutral', 'angry', 'sad', 'happy')
                OR new_gold_emotion IS NULL
            ),
            human_time_s REAL NOT NULL CHECK(human_time_s >= 0),
            reject_reason TEXT NOT NULL DEFAULT '',
            changed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS exports (
            export_id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL UNIQUE REFERENCES candidates(candidate_id),
            export_status TEXT NOT NULL CHECK(
                export_status IN ('in_progress', 'completed', 'failed')
            ),
            source_path TEXT NOT NULL,
            destination_path TEXT NOT NULL UNIQUE,
            source_hash TEXT NOT NULL,
            destination_hash TEXT,
            exported_gold_emotion TEXT NOT NULL CHECK(
                exported_gold_emotion IN ('neutral', 'angry', 'sad', 'happy')
            ),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            error_message TEXT NOT NULL DEFAULT '',
            CHECK(
                (export_status = 'completed' AND destination_hash IS NOT NULL AND completed_at IS NOT NULL)
                OR export_status != 'completed'
            )
        );

        CREATE TABLE IF NOT EXISTS export_issues (
            issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            export_id INTEGER NOT NULL REFERENCES exports(export_id),
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
            issue_type TEXT NOT NULL CHECK(
                issue_type IN ('review_status_changed', 'gold_emotion_changed')
            ),
            details TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_exports_source_hash
        ON exports(source_hash);

        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL REFERENCES videos(video_id),
            test_case_id TEXT NOT NULL,
            workflow_mode TEXT NOT NULL CHECK(workflow_mode IN ('manual', 'automated')),
            started_at TEXT NOT NULL,
            ended_at TEXT,
            elapsed_s REAL CHECK(elapsed_s IS NULL OR elapsed_s >= 0),
            success INTEGER CHECK(success IN (0, 1) OR success IS NULL),
            error_type TEXT NOT NULL DEFAULT '',
            algorithm_version TEXT NOT NULL DEFAULT '',
            model_version TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS stage_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            stage TEXT NOT NULL CHECK(stage IN (
                'preprocess', 'vad', 'stt', 'scene_detection',
                'clip_selection', 'llm_prelabel', 'human_review', 'export'
            )),
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            elapsed_s REAL NOT NULL CHECK(elapsed_s >= 0),
            success INTEGER NOT NULL CHECK(success IN (0, 1)),
            error_type TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            algorithm_version TEXT NOT NULL DEFAULT '',
            model_version TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS candidate_run_links (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
            PRIMARY KEY(run_id, candidate_id)
        );

        CREATE TABLE IF NOT EXISTS manual_interventions (
            intervention_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            intervention_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS media_info (
            video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
            duration_s REAL NOT NULL CHECK(duration_s > 0),
            width INTEGER NOT NULL CHECK(width > 0),
            height INTEGER NOT NULL CHECK(height > 0),
            frame_rate REAL NOT NULL CHECK(frame_rate > 0),
            has_audio INTEGER NOT NULL CHECK(has_audio IN (0, 1)),
            video_codec TEXT NOT NULL,
            audio_codec TEXT,
            probed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vad_analyses (
            analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            video_id TEXT NOT NULL REFERENCES videos(video_id),
            model_name TEXT NOT NULL,
            model_version TEXT NOT NULL,
            config_json TEXT NOT NULL,
            raw_segment_count INTEGER NOT NULL CHECK(raw_segment_count >= 0),
            merged_segment_count INTEGER NOT NULL CHECK(merged_segment_count >= 0),
            candidate_count INTEGER NOT NULL CHECK(candidate_count >= 0),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vad_segments (
            segment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER NOT NULL REFERENCES vad_analyses(analysis_id),
            segment_kind TEXT NOT NULL CHECK(segment_kind IN ('raw', 'merged')),
            sequence_no INTEGER NOT NULL,
            start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
            end_ms INTEGER NOT NULL CHECK(end_ms > start_ms),
            UNIQUE(analysis_id, segment_kind, sequence_no)
        );

        CREATE TABLE IF NOT EXISTS vad_proposals (
            proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id INTEGER NOT NULL REFERENCES vad_analyses(analysis_id),
            sequence_no INTEGER NOT NULL,
            start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
            end_ms INTEGER NOT NULL CHECK(end_ms > start_ms),
            accepted INTEGER NOT NULL CHECK(accepted IN (0, 1)),
            reject_reason TEXT NOT NULL DEFAULT '',
            candidate_id TEXT REFERENCES candidates(candidate_id),
            UNIQUE(analysis_id, sequence_no)
        );

        CREATE TABLE IF NOT EXISTS scene_analyses (
            scene_analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            video_id TEXT NOT NULL REFERENCES videos(video_id),
            vad_analysis_id INTEGER NOT NULL UNIQUE REFERENCES vad_analyses(analysis_id),
            library_version TEXT NOT NULL,
            policy_mode TEXT NOT NULL CHECK(policy_mode IN ('strict', 'warn')),
            config_json TEXT NOT NULL,
            hard_cut_count INTEGER NOT NULL CHECK(hard_cut_count >= 0),
            fade_count INTEGER NOT NULL CHECK(fade_count >= 0),
            transition_count INTEGER NOT NULL CHECK(transition_count >= 0),
            pre_candidate_count INTEGER NOT NULL CHECK(pre_candidate_count >= 0),
            post_candidate_count INTEGER NOT NULL CHECK(post_candidate_count >= 0),
            scene_rejected_count INTEGER NOT NULL CHECK(scene_rejected_count >= 0),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scene_transitions (
            transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_analysis_id INTEGER NOT NULL REFERENCES scene_analyses(scene_analysis_id),
            sequence_no INTEGER NOT NULL,
            time_ms INTEGER NOT NULL CHECK(time_ms >= 0),
            transition_type TEXT NOT NULL CHECK(transition_type IN ('hard_cut', 'fade')),
            detector_name TEXT NOT NULL,
            UNIQUE(scene_analysis_id, sequence_no)
        );

        CREATE TABLE IF NOT EXISTS scene_candidate_decisions (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_analysis_id INTEGER NOT NULL REFERENCES scene_analyses(scene_analysis_id),
            vad_proposal_id INTEGER NOT NULL REFERENCES vad_proposals(proposal_id),
            sequence_no INTEGER NOT NULL,
            original_start_ms INTEGER NOT NULL CHECK(original_start_ms >= 0),
            original_end_ms INTEGER NOT NULL CHECK(original_end_ms > original_start_ms),
            adjusted_start_ms INTEGER NOT NULL CHECK(adjusted_start_ms >= 0),
            adjusted_end_ms INTEGER NOT NULL CHECK(adjusted_end_ms > adjusted_start_ms),
            decision_status TEXT NOT NULL CHECK(
                decision_status IN ('accepted', 'rejected', 'warning')
            ),
            reason TEXT NOT NULL DEFAULT '',
            transition_count INTEGER NOT NULL CHECK(transition_count >= 0),
            candidate_id TEXT REFERENCES candidates(candidate_id),
            UNIQUE(scene_analysis_id, vad_proposal_id, sequence_no)
        );

        CREATE TABLE IF NOT EXISTS stt_analyses (
            stt_analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            video_id TEXT NOT NULL REFERENCES videos(video_id),
            engine_name TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            model_name TEXT NOT NULL,
            model_path TEXT NOT NULL,
            model_hash TEXT NOT NULL,
            language TEXT NOT NULL CHECK(language='ko'),
            task TEXT NOT NULL CHECK(task='transcribe'),
            segment_count INTEGER NOT NULL CHECK(segment_count >= 0),
            word_count INTEGER NOT NULL CHECK(word_count >= 0),
            uncertain INTEGER NOT NULL CHECK(uncertain IN (0, 1)),
            uncertainty_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stt_segments (
            stt_segment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stt_analysis_id INTEGER NOT NULL REFERENCES stt_analyses(stt_analysis_id),
            sequence_no INTEGER NOT NULL,
            start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
            end_ms INTEGER NOT NULL CHECK(end_ms >= start_ms),
            text TEXT NOT NULL,
            avg_logprob REAL,
            no_speech_prob REAL,
            compression_ratio REAL,
            uncertain INTEGER NOT NULL CHECK(uncertain IN (0, 1)),
            uncertainty_reason TEXT NOT NULL DEFAULT '',
            UNIQUE(stt_analysis_id, sequence_no)
        );

        CREATE TABLE IF NOT EXISTS stt_words (
            stt_word_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stt_segment_id INTEGER NOT NULL REFERENCES stt_segments(stt_segment_id),
            sequence_no INTEGER NOT NULL,
            start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
            end_ms INTEGER NOT NULL CHECK(end_ms >= start_ms),
            text TEXT NOT NULL,
            probability REAL,
            UNIQUE(stt_segment_id, sequence_no)
        );

        CREATE TABLE IF NOT EXISTS stt_vad_alignments (
            alignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stt_analysis_id INTEGER NOT NULL REFERENCES stt_analyses(stt_analysis_id),
            vad_segment_id INTEGER NOT NULL REFERENCES vad_segments(segment_id),
            stt_segment_id INTEGER NOT NULL REFERENCES stt_segments(stt_segment_id),
            overlap_ms INTEGER NOT NULL CHECK(overlap_ms >= 0),
            vad_coverage REAL NOT NULL CHECK(vad_coverage >= 0 AND vad_coverage <= 1),
            stt_coverage REAL NOT NULL CHECK(stt_coverage >= 0 AND stt_coverage <= 1),
            start_delta_ms INTEGER NOT NULL,
            end_delta_ms INTEGER NOT NULL,
            UNIQUE(stt_analysis_id, vad_segment_id, stt_segment_id)
        );

        CREATE TABLE IF NOT EXISTS clip_selection_decisions (
            selection_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
            stt_analysis_id INTEGER REFERENCES stt_analyses(stt_analysis_id),
            decision_status TEXT NOT NULL CHECK(decision_status IN ('selected','rejected','deferred')),
            score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
            reason TEXT NOT NULL,
            transcript TEXT NOT NULL DEFAULT '',
            sentence_complete INTEGER CHECK(sentence_complete IN (0, 1) OR sentence_complete IS NULL),
            vad_stt_overlap REAL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, candidate_id)
        );

        CREATE TABLE IF NOT EXISTS vad_padding_decisions (
            padding_id INTEGER PRIMARY KEY AUTOINCREMENT,
            vad_proposal_id INTEGER NOT NULL REFERENCES vad_proposals(proposal_id),
            original_start_ms INTEGER NOT NULL CHECK(original_start_ms >= 0),
            original_end_ms INTEGER NOT NULL CHECK(original_end_ms > original_start_ms),
            padded_start_ms INTEGER NOT NULL CHECK(padded_start_ms >= 0),
            padded_end_ms INTEGER NOT NULL CHECK(padded_end_ms > padded_start_ms),
            accepted INTEGER NOT NULL CHECK(accepted IN (0, 1)),
            reason TEXT NOT NULL,
            pad_before_ms INTEGER NOT NULL CHECK(pad_before_ms >= 0),
            pad_after_ms INTEGER NOT NULL CHECK(pad_after_ms >= 0),
            speech_ratio REAL NOT NULL CHECK(speech_ratio >= 0 AND speech_ratio <= 1),
            candidate_id TEXT REFERENCES candidates(candidate_id),
            UNIQUE(vad_proposal_id)
        );

        CREATE TABLE IF NOT EXISTS review_measurements (
            measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_event_id INTEGER NOT NULL UNIQUE REFERENCES review_events(event_id),
            run_id TEXT REFERENCES runs(run_id),
            started_at TEXT,
            ended_at TEXT,
            elapsed_s REAL CHECK(elapsed_s IS NULL OR elapsed_s >= 0),
            measurement_status TEXT NOT NULL CHECK(
                measurement_status IN ('measured', 'not_measured')
            ),
            measurement_source TEXT NOT NULL CHECK(
                measurement_source IN ('explicit_timestamps', 'explicit_duration', 'not_provided')
            )
        );

        CREATE TABLE IF NOT EXISTS run_candidate_outcomes (
            outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
            review_event_id INTEGER NOT NULL REFERENCES review_events(event_id),
            review_status TEXT NOT NULL CHECK(
                review_status IN ('pending', 'accepted', 'rejected', 'deferred')
            ),
            gold_emotion TEXT,
            export_valid INTEGER NOT NULL DEFAULT 0 CHECK(export_valid IN (0, 1)),
            recorded_at TEXT NOT NULL,
            UNIQUE(run_id, review_event_id)
        );

        CREATE TABLE IF NOT EXISTS metric_coverage (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            metric_name TEXT NOT NULL CHECK(metric_name IN ('manual_interventions')),
            measurement_status TEXT NOT NULL CHECK(
                measurement_status IN ('measured', 'not_measured')
            ),
            PRIMARY KEY(run_id, metric_name)
        );

        CREATE TABLE IF NOT EXISTS batch_jobs (
            batch_id TEXT PRIMARY KEY,
            config_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(
                status IN ('running', 'completed', 'completed_with_errors')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS batch_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL REFERENCES batch_jobs(batch_id),
            source_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            video_id TEXT REFERENCES videos(video_id),
            run_id TEXT REFERENCES runs(run_id),
            status TEXT NOT NULL CHECK(
                status IN ('pending', 'running', 'completed', 'failed', 'skipped_duplicate')
            ),
            duplicate_of_item_id INTEGER REFERENCES batch_items(item_id),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            candidate_count INTEGER,
            selected_count INTEGER,
            prediction_count INTEGER,
            elapsed_s REAL CHECK(elapsed_s IS NULL OR elapsed_s >= 0),
            error_type TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            completed_at TEXT,
            UNIQUE(batch_id, source_path)
        );

        CREATE INDEX IF NOT EXISTS ix_batch_items_hash
        ON batch_items(batch_id, file_hash);

        CREATE TABLE IF NOT EXISTS youtube_sources (
            youtube_video_id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL UNIQUE,
            title TEXT,
            channel_name TEXT,
            channel_url TEXT,
            thumbnail_url TEXT,
            metadata_status TEXT NOT NULL CHECK(
                metadata_status IN ('not_requested', 'succeeded', 'failed')
            ),
            metadata_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(metadata_attempt_count >= 0),
            metadata_error TEXT NOT NULL DEFAULT '',
            rights_status TEXT NOT NULL CHECK(rights_status IN ('unknown', 'approved', 'blocked')),
            file_status TEXT NOT NULL CHECK(file_status IN ('missing', 'linked', 'staged')),
            local_path TEXT,
            file_hash TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK((local_path IS NULL) = (file_hash IS NULL)),
            CHECK(rights_status != 'blocked' OR file_status = 'missing')
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_youtube_sources_file_hash
        ON youtube_sources(file_hash) WHERE file_hash IS NOT NULL;

        CREATE TABLE IF NOT EXISTS youtube_metadata_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_video_id TEXT NOT NULL REFERENCES youtube_sources(youtube_video_id),
            attempted_at TEXT NOT NULL,
            success INTEGER NOT NULL CHECK(success IN (0, 1)),
            error_type TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT ''
        );
        """
    )

    candidate_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(candidates)").fetchall()
    }
    if "clip_path" not in candidate_columns:
        connection.execute("ALTER TABLE candidates ADD COLUMN clip_path TEXT")
    if "file_hash" not in candidate_columns:
        connection.execute("ALTER TABLE candidates ADD COLUMN file_hash TEXT")

    duplicate_segment = connection.execute(
        """SELECT video_id, start_ms, end_ms, count(*) AS n
           FROM candidates GROUP BY video_id, start_ms, end_ms HAVING n > 1 LIMIT 1"""
    ).fetchone()
    if duplicate_segment is not None:
        raise RuntimeError(
            "cannot add duplicate-segment protection: existing duplicate candidates found"
        )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_candidates_segment
           ON candidates(video_id, start_ms, end_ms)"""
    )

    if existing_version is None:
        connection.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))
    elif existing_version in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
        connection.execute("UPDATE schema_info SET version = ?", (SCHEMA_VERSION,))
    connection.commit()


def insert_video(connection: sqlite3.Connection, video: VideoRecord) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO videos (
                video_id, local_path, file_hash, source_name, rights_status,
                rights_basis, allowed_use, duration_s, added_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video.video_id, video.local_path, video.file_hash, video.source_name,
                video.rights_status.value, video.rights_basis, video.allowed_use,
                video.duration_s, video.added_at, video.notes,
            ),
        )


def find_video_by_hash(connection: sqlite3.Connection, file_hash: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM videos WHERE file_hash = ?", (file_hash,)).fetchone()


def insert_candidate(
    connection: sqlite3.Connection,
    candidate: CandidateRecord,
    *,
    clip_path: str,
    file_hash: str,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO candidates (
                candidate_id, video_id, start_ms, end_ms, status, transcript,
                algorithm_version, clip_path, file_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.candidate_id, candidate.video_id, candidate.start_ms,
                candidate.end_ms, candidate.status.value, candidate.transcript,
                candidate.algorithm_version, clip_path, file_hash,
            ),
        )


def insert_prediction(connection: sqlite3.Connection, prediction: PredictionRecord) -> int:
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO predictions (
                candidate_id, emotion, model_name, prompt_version, elapsed_s, needs_review
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                prediction.candidate_id,
                prediction.emotion.value if prediction.emotion is not None else None,
                prediction.model_name,
                prediction.prompt_version,
                prediction.elapsed_s,
                int(prediction.needs_review),
            ),
        )
    return int(cursor.lastrowid)


def register_video_from_manifest(
    connection: sqlite3.Connection,
    project_root: Path,
    row: ManifestRow,
    *,
    added_at: str | None = None,
) -> VideoRecord:
    """Safely resolve, hash, and insert one already validated manifest row."""
    source = resolve_manifest_input(project_root, row.local_path)
    video = VideoRecord(
        video_id=row.video_id,
        local_path=row.local_path,
        file_hash=sha256_file(source),
        source_name=row.source_name,
        rights_status=row.rights_status,
        rights_basis=row.rights_basis,
        allowed_use=row.allowed_use,
        duration_s=row.duration_s,
        added_at=added_at or datetime.now(timezone.utc).isoformat(),
        notes=row.notes,
    )
    insert_video(connection, video)
    return video
