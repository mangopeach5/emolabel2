from __future__ import annotations

import sqlite3


def scene_analysis_summary(connection: sqlite3.Connection, scene_analysis_id: int) -> dict:
    analysis = connection.execute(
        "SELECT * FROM scene_analyses WHERE scene_analysis_id=?", (scene_analysis_id,)
    ).fetchone()
    if analysis is None:
        raise LookupError(f"scene analysis not found: {scene_analysis_id}")
    review = connection.execute(
        """SELECT
               count(r.candidate_id) AS reviewed_count,
               coalesce(sum(CASE WHEN r.review_status='accepted' THEN 1 ELSE 0 END), 0)
                   AS human_accepted_count
           FROM scene_candidate_decisions d
           LEFT JOIN reviews r ON r.candidate_id=d.candidate_id
           WHERE d.scene_analysis_id=? AND d.candidate_id IS NOT NULL""",
        (scene_analysis_id,),
    ).fetchone()
    reviewed = int(review["reviewed_count"])
    accepted = int(review["human_accepted_count"])
    return {
        "scene_analysis_id": scene_analysis_id,
        "pre_candidate_count": analysis["pre_candidate_count"],
        "post_candidate_count": analysis["post_candidate_count"],
        "scene_rejected_count": analysis["scene_rejected_count"],
        "hard_cut_count": analysis["hard_cut_count"],
        "fade_count": analysis["fade_count"],
        "reviewed_count": reviewed,
        "human_accepted_count": accepted,
        "human_adoption_rate": accepted / reviewed if reviewed else None,
    }
