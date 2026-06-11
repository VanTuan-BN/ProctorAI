import logging
import os
import mysql.connector


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
DB_HOST = os.getenv("S_MONITOR_DB_HOST", "127.0.0.1")
DB_USER = os.getenv("S_MONITOR_DB_USER", "root")
DB_PASSWORD = os.getenv("S_MONITOR_DB_PASSWORD", "12345")
DB_NAME = os.getenv("S_MONITOR_DB_NAME", "exam_monitor_db")


def get_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )


def column_exists(cur, table_name, column_name):
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    return cur.fetchone()[0] > 0


def foreign_key_exists(cur, table_name, constraint_name):
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.TABLE_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s AND CONSTRAINT_NAME = %s
        """,
        (table_name, constraint_name),
    )
    return cur.fetchone()[0] > 0


def index_exists(cur, table_name, index_name):
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s
        """,
        (table_name, index_name),
    )
    return cur.fetchone()[0] > 0


def get_column_metadata(cur, table_name, column_name):
    cur.execute(
        """
        SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    return cur.fetchone()


def ensure_column(cur, table_name, column_name, ddl):
    if not column_exists(cur, table_name, column_name):
        logger.info("Adding %s.%s", table_name, column_name)
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def ensure_password_column_capacity(cur, table_name, column_name="password", target_length=255):
    metadata = get_column_metadata(cur, table_name, column_name)
    if not metadata:
        return
    data_type, current_length, is_nullable = metadata
    if str(data_type).lower() not in {"varchar", "char"}:
        return
    current_length = int(current_length or 0)
    if current_length >= target_length:
        return
    nullable_sql = "NULL" if str(is_nullable).upper() == "YES" else "NOT NULL"
    logger.info("Expanding %s.%s to VARCHAR(%s)", table_name, column_name, target_length)
    cur.execute(f"ALTER TABLE {table_name} MODIFY COLUMN {column_name} VARCHAR({target_length}) {nullable_sql}")


def get_default_proctor_id(cur):
    cur.execute("SELECT proctor_id FROM proctors ORDER BY proctor_id LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


def ensure_default_template(cur):
    cur.execute("SELECT template_id FROM exam_templates ORDER BY template_id LIMIT 1")
    row = cur.fetchone()
    if row:
        return row[0]

    proctor_id = get_default_proctor_id(cur)
    logger.info("Creating default migrated exam template")
    cur.execute(
        "INSERT INTO exam_templates (template_name, exam_name, proctor_id) VALUES (%s, %s, %s)",
        ("Migrated Default Template", "Migrated Default Template", proctor_id),
    )
    return cur.lastrowid


def backfill_templates(cur, default_template_id):
    cur.execute("UPDATE exam_templates SET template_name = COALESCE(template_name, exam_name, %s) WHERE template_name IS NULL", ("Migrated Template",))

    default_proctor_id = get_default_proctor_id(cur)
    if default_proctor_id:
        cur.execute("UPDATE exam_templates SET proctor_id = %s WHERE proctor_id IS NULL", (default_proctor_id,))

    cur.execute("UPDATE classes SET template_id = %s WHERE template_id IS NULL", (default_template_id,))
    cur.execute("UPDATE question_bank SET template_id = %s WHERE template_id IS NULL", (default_template_id,))

    cur.execute(
        """
        INSERT IGNORE INTO template_questions (template_id, q_id)
        SELECT template_id, q_id
        FROM question_bank
        WHERE template_id IS NOT NULL
        """
    )

    cur.execute("SELECT exam_id FROM exams ORDER BY exam_id")
    existing_exam_ids = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT class_id, class_name, created_at FROM classes")
    for class_id, class_name, created_at in cur.fetchall():
        if class_id in existing_exam_ids:
            continue
        cur.execute(
            "INSERT INTO exams (exam_id, subject, exam_date, room, template_id) VALUES (%s, %s, %s, %s, %s)",
            (class_id, class_name, created_at.date() if created_at else None, "Migrated", default_template_id),
        )


def align_foreign_keys(cur):
    if foreign_key_exists(cur, "exam_results", "exam_results_ibfk_2"):
        logger.info("Repointing exam_results.exam_id foreign key to classes.class_id")
        cur.execute("ALTER TABLE exam_results DROP FOREIGN KEY exam_results_ibfk_2")
    if foreign_key_exists(cur, "violations", "violations_ibfk_2"):
        logger.info("Repointing violations.exam_id foreign key to classes.class_id")
        cur.execute("ALTER TABLE violations DROP FOREIGN KEY violations_ibfk_2")

    if not index_exists(cur, "exam_results", "exam_id"):
        cur.execute("ALTER TABLE exam_results ADD INDEX exam_id (exam_id)")
    if not index_exists(cur, "violations", "exam_id"):
        cur.execute("ALTER TABLE violations ADD INDEX exam_id (exam_id)")

    if not foreign_key_exists(cur, "exam_results", "fk_exam_results_class"):
        cur.execute(
            "ALTER TABLE exam_results ADD CONSTRAINT fk_exam_results_class FOREIGN KEY (exam_id) REFERENCES classes(class_id)"
        )
    if not foreign_key_exists(cur, "violations", "fk_violations_class"):
        cur.execute(
            "ALTER TABLE violations ADD CONSTRAINT fk_violations_class FOREIGN KEY (exam_id) REFERENCES classes(class_id) ON DELETE CASCADE"
        )


def create_yolo_world_tables(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS yolo_world_tasks (
            task_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            session_token VARCHAR(128) NULL,
            msv VARCHAR(20) NOT NULL,
            exam_id INT NOT NULL,
            violation_id INT NULL,
            evidence_path VARCHAR(255) NOT NULL,
            trigger_type VARCHAR(64) NOT NULL,
            source VARCHAR(32) NOT NULL DEFAULT 'violation_upload',
            prompt_profile VARCHAR(64) NOT NULL DEFAULT 'default_exam',
            input_meta_json JSON NULL,
            status ENUM('pending','processing','done','failed','cancelled') NOT NULL DEFAULT 'pending',
            priority TINYINT NOT NULL DEFAULT 5,
            attempt_count INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 3,
            next_retry_at DATETIME NULL,
            locked_by VARCHAR(64) NULL,
            locked_at DATETIME NULL,
            error_message TEXT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            processed_at DATETIME NULL,
            PRIMARY KEY (task_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS yolo_world_results (
            result_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            task_id BIGINT UNSIGNED NOT NULL,
            violation_id INT NULL,
            exam_id INT NOT NULL,
            msv VARCHAR(20) NOT NULL,
            top_label VARCHAR(128) NULL,
            top_confidence DECIMAL(6,4) NULL,
            verdict VARCHAR(32) NOT NULL DEFAULT 'review',
            risk_delta DECIMAL(6,4) NOT NULL DEFAULT 0.0000,
            labels_json JSON NULL,
            boxes_json JSON NULL,
            output_meta_json JSON NULL,
            model_name VARCHAR(128) NOT NULL DEFAULT 'yolo_world',
            model_version VARCHAR(64) NULL,
            prompt_used TEXT NULL,
            inference_ms INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (result_id),
            CONSTRAINT fk_yw_results_task FOREIGN KEY (task_id) REFERENCES yolo_world_tasks(task_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    if not index_exists(cur, "yolo_world_tasks", "idx_yw_tasks_status_priority_created"):
        cur.execute("ALTER TABLE yolo_world_tasks ADD INDEX idx_yw_tasks_status_priority_created (status, priority, created_at)")
    if not index_exists(cur, "yolo_world_tasks", "idx_yw_tasks_exam_msv"):
        cur.execute("ALTER TABLE yolo_world_tasks ADD INDEX idx_yw_tasks_exam_msv (exam_id, msv)")
    if not index_exists(cur, "yolo_world_tasks", "idx_yw_tasks_session"):
        cur.execute("ALTER TABLE yolo_world_tasks ADD INDEX idx_yw_tasks_session (session_token)")
    if not index_exists(cur, "yolo_world_tasks", "idx_yw_tasks_violation"):
        cur.execute("ALTER TABLE yolo_world_tasks ADD INDEX idx_yw_tasks_violation (violation_id)")

    if not index_exists(cur, "yolo_world_results", "idx_yw_results_task"):
        cur.execute("ALTER TABLE yolo_world_results ADD INDEX idx_yw_results_task (task_id)")
    if not index_exists(cur, "yolo_world_results", "idx_yw_results_exam_msv"):
        cur.execute("ALTER TABLE yolo_world_results ADD INDEX idx_yw_results_exam_msv (exam_id, msv)")
    if not index_exists(cur, "yolo_world_results", "idx_yw_results_violation"):
        cur.execute("ALTER TABLE yolo_world_results ADD INDEX idx_yw_results_violation (violation_id)")


def create_password_recovery_tables(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_requests (
            request_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            account_role ENUM('student','proctor','admin') NOT NULL,
            account_id VARCHAR(64) NOT NULL,
            full_name VARCHAR(255) NOT NULL,
            request_note VARCHAR(255) NULL,
            status ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
            resolved_note VARCHAR(255) NULL,
            approved_by VARCHAR(128) NULL,
            approved_at DATETIME NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (request_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    if not index_exists(cur, "password_reset_requests", "idx_password_reset_status_created"):
        cur.execute("ALTER TABLE password_reset_requests ADD INDEX idx_password_reset_status_created (status, created_at)")
    if not index_exists(cur, "password_reset_requests", "idx_password_reset_account"):
        cur.execute("ALTER TABLE password_reset_requests ADD INDEX idx_password_reset_account (account_role, account_id)")


def create_student_face_reference_tables(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS student_face_images (
            ref_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            msv VARCHAR(20) NOT NULL,
            face_image VARCHAR(255) NOT NULL,
            is_primary TINYINT(1) NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ref_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    if not index_exists(cur, "student_face_images", "idx_student_face_images_msv"):
        cur.execute("ALTER TABLE student_face_images ADD INDEX idx_student_face_images_msv (msv)")
    if not index_exists(cur, "student_face_images", "idx_student_face_images_primary"):
        cur.execute("ALTER TABLE student_face_images ADD INDEX idx_student_face_images_primary (msv, is_primary)")
    if not index_exists(cur, "student_face_images", "uq_student_face_image"):
        cur.execute("ALTER TABLE student_face_images ADD UNIQUE INDEX uq_student_face_image (msv, face_image)")
    if not foreign_key_exists(cur, "student_face_images", "fk_student_face_images_student"):
        cur.execute(
            "ALTER TABLE student_face_images ADD CONSTRAINT fk_student_face_images_student FOREIGN KEY (msv) REFERENCES students(msv) ON DELETE CASCADE"
        )

    cur.execute(
        """
        INSERT IGNORE INTO student_face_images (msv, face_image, is_primary)
        SELECT s.msv, s.face_image, 1
        FROM students s
        WHERE s.face_image IS NOT NULL AND TRIM(s.face_image) <> ''
        """
    )
    cur.execute(
        """
        UPDATE student_face_images refs
        JOIN students s ON s.msv = refs.msv
        SET refs.is_primary = CASE WHEN refs.face_image = s.face_image THEN 1 ELSE 0 END
        WHERE s.face_image IS NOT NULL AND TRIM(s.face_image) <> ''
        """
    )
    cur.execute(
        """
        UPDATE student_face_images refs
        LEFT JOIN (
            SELECT msv, MIN(ref_id) AS first_ref_id
            FROM student_face_images
            GROUP BY msv
        ) ranked ON ranked.msv = refs.msv
        LEFT JOIN (
            SELECT DISTINCT msv
            FROM student_face_images
            WHERE is_primary = 1
        ) primary_refs ON primary_refs.msv = refs.msv
        SET refs.is_primary = CASE
            WHEN primary_refs.msv IS NULL AND refs.ref_id = ranked.first_ref_id THEN 1
            ELSE refs.is_primary
        END
        """
    )


def main():
    conn = get_connection()
    try:
        cur = conn.cursor()

        ensure_column(cur, "exam_templates", "template_name", "template_name VARCHAR(100) NULL AFTER template_id")
        ensure_column(cur, "exam_templates", "proctor_id", "proctor_id VARCHAR(20) NULL AFTER template_name")
        ensure_column(cur, "classes", "template_id", "template_id INT NULL AFTER duration_minutes")
        ensure_column(cur, "question_bank", "template_id", "template_id INT NULL AFTER q_id")
        ensure_password_column_capacity(cur, "students")
        ensure_password_column_capacity(cur, "proctors")
        ensure_password_column_capacity(cur, "admins")

        if not index_exists(cur, "classes", "idx_classes_template_id"):
            cur.execute("ALTER TABLE classes ADD INDEX idx_classes_template_id (template_id)")
        if not index_exists(cur, "question_bank", "idx_question_bank_template_id"):
            cur.execute("ALTER TABLE question_bank ADD INDEX idx_question_bank_template_id (template_id)")

        if not foreign_key_exists(cur, "exam_templates", "fk_exam_templates_proctor") and column_exists(cur, "exam_templates", "proctor_id"):
            cur.execute(
                "ALTER TABLE exam_templates ADD CONSTRAINT fk_exam_templates_proctor FOREIGN KEY (proctor_id) REFERENCES proctors(proctor_id)"
            )

        if not foreign_key_exists(cur, "classes", "fk_classes_template") and column_exists(cur, "classes", "template_id"):
            cur.execute(
                "ALTER TABLE classes ADD CONSTRAINT fk_classes_template FOREIGN KEY (template_id) REFERENCES exam_templates(template_id)"
            )

        if not foreign_key_exists(cur, "question_bank", "fk_question_bank_template") and column_exists(cur, "question_bank", "template_id"):
            cur.execute(
                "ALTER TABLE question_bank ADD CONSTRAINT fk_question_bank_template FOREIGN KEY (template_id) REFERENCES exam_templates(template_id)"
            )

        default_template_id = ensure_default_template(cur)
        backfill_templates(cur, default_template_id)
        align_foreign_keys(cur)
        create_yolo_world_tables(cur)
        create_password_recovery_tables(cur)
        create_student_face_reference_tables(cur)

        # Phương án 1 – Event Clip Evidence: mở rộng bảng violations
        ensure_column(cur, "violations", "clip_path",          "clip_path VARCHAR(255) NULL AFTER evidence_path")
        ensure_column(cur, "violations", "thumbnail_path",     "thumbnail_path VARCHAR(255) NULL AFTER clip_path")
        ensure_column(cur, "violations", "event_started_at",   "event_started_at DATETIME NULL AFTER thumbnail_path")
        ensure_column(cur, "violations", "event_ended_at",     "event_ended_at DATETIME NULL AFTER event_started_at")
        ensure_column(cur, "violations", "duration_seconds",   "duration_seconds DECIMAL(6,2) NULL AFTER event_ended_at")
        ensure_column(cur, "violations", "review_status",      "review_status VARCHAR(16) NULL AFTER duration_seconds")
        ensure_column(cur, "violations", "review_note",        "review_note VARCHAR(255) NULL AFTER review_status")
        ensure_column(cur, "violations", "reviewed_by",        "reviewed_by VARCHAR(64) NULL AFTER review_note")
        ensure_column(cur, "violations", "reviewed_at",        "reviewed_at DATETIME NULL AFTER reviewed_by")

        conn.commit()
        logger.info("Schema migration completed successfully")
    except Exception:
        conn.rollback()
        logger.exception("Schema migration failed")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()