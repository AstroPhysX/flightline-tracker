from __future__ import annotations

from sqlalchemy import inspect, text


def ensure_schema_extensions(engine) -> None:
    """Tiny SQLite migration layer for users upgrading an existing tracker DB."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    changes = {
        "airports": [("city", "VARCHAR(120)")],
        "trips": [("ups_trip_id", "INTEGER")],
        "flights": [
            ("scheduled_rest_minutes", "INTEGER"),
            ("last_provider_poll_utc", "DATETIME"),
            ("last_track_poll_utc", "DATETIME"),
            ("last_reassignment_search_utc", "DATETIME"),
            ("provider_track_fetched", "BOOLEAN DEFAULT 0"),
            ("schedule_active", "BOOLEAN DEFAULT 1"),
            ("schedule_added", "BOOLEAN DEFAULT 0"),
            ("awarded_sequence", "INTEGER"),
            ("awarded_flight_number", "VARCHAR(24)"),
            ("awarded_flight_date", "DATE"),
            ("awarded_origin", "VARCHAR(8)"),
            ("awarded_destination", "VARCHAR(8)"),
            ("awarded_deadhead", "BOOLEAN"),
            ("awarded_scheduled_departure_utc", "DATETIME"),
            ("awarded_scheduled_arrival_utc", "DATETIME"),
            ("schedule_change_note", "TEXT"),
        ],
    }
    with engine.begin() as conn:
        for table, cols in changes.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspect(engine).get_columns(table)}
            for name, sql_type in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
        # Existing v5 databases predate awarded/current schedule separation.
        # Seed a baseline snapshot from their current values once.
        if "flights" in tables:
            conn.execute(text(
                "UPDATE flights SET "
                "schedule_active = COALESCE(schedule_active, 1), "
                "schedule_added = COALESCE(schedule_added, 0), "
                "awarded_sequence = COALESCE(awarded_sequence, sequence), "
                "awarded_flight_number = COALESCE(awarded_flight_number, flight_number), "
                "awarded_flight_date = COALESCE(awarded_flight_date, flight_date), "
                "awarded_origin = COALESCE(awarded_origin, origin), "
                "awarded_destination = COALESCE(awarded_destination, destination), "
                "awarded_deadhead = COALESCE(awarded_deadhead, deadhead), "
                "awarded_scheduled_departure_utc = COALESCE(awarded_scheduled_departure_utc, scheduled_departure_utc), "
                "awarded_scheduled_arrival_utc = COALESCE(awarded_scheduled_arrival_utc, scheduled_arrival_utc) "
                "WHERE awarded_flight_number IS NULL AND schedule_added = 0"
            ))

