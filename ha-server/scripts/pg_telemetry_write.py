#!/usr/bin/env python3
"""Upsert one hourly telemetry batch into local Postgres. Never prints secrets."""

import base64
import json
import sys
from datetime import datetime, timezone

import psycopg2


def load_secrets(path="/config/secrets.yaml"):
    secrets = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#") or line[:1].isspace() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            secrets[key.strip()] = value.strip().strip("'\"")
    return secrets


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("missing payload_b64", file=sys.stderr)
        return 2
    try:
        payload = json.loads(base64.b64decode(sys.argv[1].strip()))
    except Exception as exc:
        print(f"bad payload: {type(exc).__name__}", file=sys.stderr)
        return 2
    rows = payload.get("rows") or []
    if not rows:
        print("no rows")
        return 0
    site = payload.get("site") or "ha-server"
    when = datetime.fromtimestamp(int(payload["time"]), timezone.utc)
    secrets = load_secrets()
    password = secrets.get("postgres_password", "")
    if not password:
        print("missing postgres_password", file=sys.stderr)
        return 2
    try:
        conn = psycopg2.connect(
            host=secrets.get("postgres_host") or "db21ed7f-postgres-latest",
            port=5432,
            dbname=secrets.get("postgres_db") or "ha-metrics",
            user=secrets.get("postgres_user") or "postgres",
            password=password,
            sslmode="prefer",
            connect_timeout=8,
        )
    except Exception as exc:
        print(f"connect failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    sql = """
        INSERT INTO public.telemetry
            ("time", site, metric, value, unit, device_class, state_class)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT ("time", site, metric) DO UPDATE SET
            value = EXCLUDED.value,
            unit = EXCLUDED.unit,
            device_class = EXCLUDED.device_class,
            state_class = EXCLUDED.state_class
    """
    try:
        with conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        sql,
                        (
                            when,
                            site,
                            row["metric"],
                            float(row["value"]),
                            row.get("unit"),
                            row.get("device_class"),
                            row.get("state_class"),
                        ),
                    )
        print(f"upserted {len(rows)} at {when.isoformat()} site={site}")
        return 0
    except Exception as exc:
        print(f"write failed: {type(exc).__name__}: {str(exc).splitlines()[0][:180]}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
