"""Local command-line interface for the complete EmoLabel 2.0 MVP."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from emolabel2.analytics.reports import build_report, write_csv_report, write_json_report
from emolabel2.analytics.runtime import (
    Stage, create_run, finish_run, link_candidate,
    mark_manual_interventions_measured, record_intervention, record_stage,
)
from emolabel2.batch.pipeline import BatchConfig, batch_report, run_batch, write_batch_reports
from emolabel2.domain import Emotion, ReviewRecord, ReviewStatus, RightsStatus
from emolabel2.intake.manifest import ManifestRow
from emolabel2.media.probe import probe_media
from emolabel2.paths import resolve_inside_project
from emolabel2.pipeline import run_automatic_pipeline
from emolabel2.review.workflow import save_review_and_export_if_accepted
from emolabel2.storage import initialize_schema, open_database, register_video_from_manifest
from emolabel2.youtube.intake import (
    link_and_stage, read_link_manifest, read_url_file, refresh_metadata, register_urls,
    set_rights_status, source_report, write_source_reports,
)


def _project_path(root: Path, value: str, *, must_exist: bool = False) -> Path:
    path = resolve_inside_project(root, value, must_exist=must_exist)
    if not path.parent.is_dir():
        raise ValueError(f"parent directory does not exist: {path.parent}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emolabel2", description="Local EmoLabel 2.0 MVP")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--database", default="data/emolabel2.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    register = commands.add_parser("register", help="register approved media under data/inbox")
    register.add_argument("--video-id", required=True)
    register.add_argument("--path", required=True)
    register.add_argument("--source-name", default="local-authorized")
    register.add_argument("--rights-basis", required=True)
    register.add_argument("--allowed-use", required=True)
    analyze = commands.add_parser("analyze", help="run the automatic local pipeline")
    analyze.add_argument("--video-id", required=True)
    analyze.add_argument("--run-id")
    analyze.add_argument("--whisper-model", choices=("tiny", "base", "small"), default="base")
    analyze.add_argument("--whisper-model-path")
    analyze.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    analyze.add_argument("--scene-policy", choices=("strict", "warn"), default="strict")
    analyze.add_argument("--ollama-model", default="qwen2.5:7b")
    analyze.add_argument("--skip-emotion", action="store_true")
    candidates = commands.add_parser("candidates")
    candidates.add_argument("--run-id")
    review = commands.add_parser("review")
    review.add_argument("--candidate-id", required=True)
    review.add_argument("--status", choices=[item.value for item in ReviewStatus], required=True)
    review.add_argument("--emotion", choices=[item.value for item in Emotion])
    review.add_argument("--run-id", help="run that this review evaluates")
    review.add_argument("--human-time", type=float)
    review.add_argument("--review-started-at", help="ISO-8601 timestamp")
    review.add_argument("--review-ended-at", help="ISO-8601 timestamp")
    review.add_argument("--reason", default="")
    manual = commands.add_parser("record-manual-baseline")
    manual.add_argument("--run-id", required=True)
    manual.add_argument("--video-id", required=True)
    manual.add_argument("--test-case-id", required=True)
    manual.add_argument("--processing-s", type=float, required=True)
    manual.add_argument("--human-review-s", type=float, required=True)
    manual.add_argument("--manual-interventions", type=int)
    manual.add_argument("--candidate-id", action="append", default=[])
    batch = commands.add_parser("batch", help="process data/inbox/batch/*.mp4 with restart support")
    batch.add_argument("--batch-id", required=True)
    batch.add_argument("--rights-basis", required=True)
    batch.add_argument("--allowed-use", required=True)
    batch.add_argument("--whisper-model", choices=("tiny", "base", "small"), default="base")
    batch.add_argument("--whisper-model-path")
    batch.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    batch.add_argument("--scene-policy", choices=("strict", "warn"), default="strict")
    batch.add_argument("--ollama-model", default="qwen2.5:1.5b")
    batch.add_argument("--skip-emotion", action="store_true")
    batch.add_argument("--json-output")
    batch.add_argument("--csv-output")
    batch_status = commands.add_parser("batch-status")
    batch_status.add_argument("--batch-id", required=True)
    youtube_intake = commands.add_parser("youtube-intake", help="register YouTube URLs without downloading media")
    youtube_intake.add_argument("--input", help="TXT file inside this project")
    youtube_intake.add_argument("--fetch-metadata", action="store_true")
    youtube_intake.add_argument("--retry-failed", action="store_true")
    youtube_intake.add_argument("--json-output")
    youtube_intake.add_argument("--csv-output")
    youtube_rights = commands.add_parser("youtube-rights")
    youtube_rights.add_argument("--video-id", required=True)
    youtube_rights.add_argument("--status", choices=("unknown", "approved", "blocked"), required=True)
    youtube_link = commands.add_parser("youtube-link", help="link approved MP4 files and stage them for batch")
    youtube_link.add_argument("--manifest", required=True)
    youtube_link.add_argument("--batch-id", help="run the existing batch pipeline after staging")
    youtube_link.add_argument("--whisper-model", choices=("tiny", "base", "small"), default="base")
    youtube_link.add_argument("--whisper-model-path")
    youtube_link.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    youtube_link.add_argument("--scene-policy", choices=("strict", "warn"), default="strict")
    youtube_link.add_argument("--ollama-model", default="qwen2.5:1.5b")
    youtube_link.add_argument("--skip-emotion", action="store_true")
    stats = commands.add_parser("stats")
    stats.add_argument("--json-output")
    stats.add_argument("--csv-output")
    return parser


def _print_candidates(connection, run_id: str | None) -> None:
    condition, parameters = ("WHERE l.run_id=?", [run_id]) if run_id else ("", [])
    rows = connection.execute(
        f"""SELECT c.candidate_id, c.video_id, c.start_ms, c.end_ms, c.transcript,
                   c.status, l.run_id, s.decision_status, s.score, s.reason,
                   p.emotion AS predicted_emotion, p.model_name
            FROM candidates c
            LEFT JOIN candidate_run_links l ON l.candidate_id=c.candidate_id
            LEFT JOIN clip_selection_decisions s
              ON s.candidate_id=c.candidate_id AND s.run_id=l.run_id
            LEFT JOIN predictions p ON p.prediction_id=(
                SELECT max(p2.prediction_id) FROM predictions p2 WHERE p2.candidate_id=c.candidate_id
            ) {condition} ORDER BY c.video_id, c.start_ms""", parameters,
    ).fetchall()
    print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.project_root).resolve(strict=True)
    database = _project_path(root, args.database)
    connection = open_database(database)
    try:
        initialize_schema(connection)
        if args.command == "init-db":
            output = {"database": str(database), "status": "initialized"}
        elif args.command == "register":
            source = _project_path(root, args.path, must_exist=True)
            info = probe_media(source)
            video = register_video_from_manifest(connection, root, ManifestRow(
                args.video_id, args.path, args.source_name,
                RightsStatus.APPROVED_RESEARCH, args.rights_basis,
                args.allowed_use, info.duration_s,
            ))
            output = {"video_id": video.video_id, "duration_s": video.duration_s}
        elif args.command == "analyze":
            output = run_automatic_pipeline(
                connection, root, video_id=args.video_id, run_id=args.run_id,
                whisper_model=args.whisper_model,
                whisper_model_path=(
                    args.whisper_model_path or f"models/whisper/{args.whisper_model}.pt"
                ), device=args.device,
                scene_policy=args.scene_policy, ollama_model=args.ollama_model,
                skip_emotion=args.skip_emotion,
            )
        elif args.command == "candidates":
            _print_candidates(connection, args.run_id)
            return 0
        elif args.command == "review":
            status = ReviewStatus(args.status)
            if (args.review_started_at is None) != (args.review_ended_at is None):
                raise ValueError("review start and end timestamps must be provided together")
            if args.review_started_at is not None:
                started = datetime.fromisoformat(args.review_started_at)
                ended = datetime.fromisoformat(args.review_ended_at)
                human_time = (ended - started).total_seconds()
                if human_time < 0:
                    raise ValueError("review end must not precede start")
            else:
                human_time = args.human_time if args.human_time is not None else 0
            result = save_review_and_export_if_accepted(connection, root, ReviewRecord(
                args.candidate_id, status,
                Emotion(args.emotion) if args.emotion else None,
                human_time, datetime.now(timezone.utc).isoformat(), args.reason,
            ), run_id=args.run_id,
                review_started_at=args.review_started_at,
                review_ended_at=args.review_ended_at,
                human_time_measured=args.human_time is not None,
            )
            output = {
                "candidate_id": args.candidate_id, "status": status.value,
                "exported": str(result.destination) if result else None,
            }
        elif args.command == "record-manual-baseline":
            if args.processing_s < 0 or args.human_review_s < 0:
                raise ValueError("measured times must be nonnegative")
            if args.manual_interventions is not None and args.manual_interventions < 0:
                raise ValueError("manual interventions must be nonnegative")
            now = datetime.now(timezone.utc).isoformat()
            create_run(
                connection, run_id=args.run_id, video_id=args.video_id,
                test_case_id=args.test_case_id, workflow_mode="manual", started_at=now,
            )
            record_stage(
                connection, run_id=args.run_id, stage=Stage.HUMAN_REVIEW,
                started_at=now, ended_at=now, elapsed_s=args.human_review_s,
                success=True, algorithm_version="manual-measurement-v1",
            )
            for candidate_id in args.candidate_id:
                link_candidate(connection, args.run_id, candidate_id)
            if args.manual_interventions is not None:
                mark_manual_interventions_measured(connection, args.run_id)
                for index in range(args.manual_interventions):
                    record_intervention(
                        connection, args.run_id, "manual_baseline_action",
                        details=f"explicitly counted action {index + 1}", occurred_at=now,
                    )
            finish_run(
                connection, args.run_id, elapsed_s=args.processing_s,
                success=True, ended_at=now,
            )
            output = {
                "run_id": args.run_id, "workflow_mode": "manual",
                "human_review_s": args.human_review_s,
                "manual_interventions": args.manual_interventions,
            }
        elif args.command == "batch":
            if bool(args.json_output) != bool(args.csv_output):
                raise ValueError("JSON and CSV output paths must be provided together")
            output = run_batch(
                connection, root, batch_id=args.batch_id,
                config=BatchConfig(
                    rights_basis=args.rights_basis, allowed_use=args.allowed_use,
                    whisper_model=args.whisper_model,
                    whisper_model_path=(
                        args.whisper_model_path or f"models/whisper/{args.whisper_model}.pt"
                    ),
                    device=args.device, scene_policy=args.scene_policy,
                    ollama_model=args.ollama_model, skip_emotion=args.skip_emotion,
                ),
            )
            if args.json_output:
                write_batch_reports(
                    output, _project_path(root, args.json_output),
                    _project_path(root, args.csv_output),
                )
        elif args.command == "batch-status":
            output = batch_report(connection, args.batch_id)
        elif args.command == "youtube-intake":
            if bool(args.json_output) != bool(args.csv_output):
                raise ValueError("JSON and CSV output paths must be provided together")
            if not args.input and not args.retry_failed:
                raise ValueError("--input is required unless --retry-failed is used")
            entries, failures = read_url_file(root, args.input) if args.input else ([], [])
            register_urls(connection, entries)
            if args.fetch_metadata or args.retry_failed:
                refresh_metadata(connection, failed_only=args.retry_failed)
            output = source_report(connection, failures)
            if args.json_output:
                write_source_reports(
                    output, _project_path(root, args.json_output),
                    _project_path(root, args.csv_output),
                )
        elif args.command == "youtube-rights":
            set_rights_status(connection, args.video_id, args.status)
            output = {"youtube_video_id": args.video_id, "rights_status": args.status}
        elif args.command == "youtube-link":
            rows = read_link_manifest(root, args.manifest)
            if args.batch_id:
                rights_basis = {row["rights_basis"] for row in rows}
                allowed_use = {row["allowed_use"] for row in rows}
                if len(rights_basis) != 1 or len(allowed_use) != 1:
                    raise ValueError("one automatic batch requires the same rights_basis and allowed_use")
            staged = link_and_stage(connection, root, rows)
            output = {"staged": staged, "batch": None}
            if args.batch_id:
                output["batch"] = run_batch(
                    connection, root, batch_id=args.batch_id,
                    config=BatchConfig(
                        rights_basis=next(iter(rights_basis)), allowed_use=next(iter(allowed_use)),
                        whisper_model=args.whisper_model,
                        whisper_model_path=(args.whisper_model_path or f"models/whisper/{args.whisper_model}.pt"),
                        device=args.device, scene_policy=args.scene_policy,
                        ollama_model=args.ollama_model, skip_emotion=args.skip_emotion,
                    ),
                )
        else:
            report = build_report(connection, root)
            if args.json_output:
                write_json_report(report, _project_path(root, args.json_output))
            if args.csv_output:
                write_csv_report(report, _project_path(root, args.csv_output))
            output = report
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
