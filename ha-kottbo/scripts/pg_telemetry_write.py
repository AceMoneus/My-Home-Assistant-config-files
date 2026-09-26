#!/usr/bin/env python3
import base64, json, sys
from datetime import datetime, timezone
import psycopg2

def secrets(path="/config/secrets.yaml"):
    out = {}
    for line in open(path, encoding="utf-8"):
        if not line.strip() or line[0] in "# " or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out

def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("missing payload_b64", file=sys.stderr)
        return 2
    try:
        payload = json.loads(base64.b64decode(sys.argv[1].strip()))
    except Exception as exc:
        print("bad payload: %s" % type(exc).__name__, file=sys.stderr)
        return 2
    rows = payload.get("rows") or []
    if not rows:
        print("no rows")
        return 0
    site = payload.get("site") or "ha-kottbo"
    when = datetime.fromtimestamp(int(payload["time"]), timezone.utc)
    sec = secrets()
    password = sec.get("postgres_password", "")
    if not password:
        print("missing postgres_password", file=sys.stderr)
        return 2
    try:
        conn = psycopg2.connect(host=sec.get("postgres_host") or "db21ed7f-postgres-latest", port=5432, dbname=sec.get("postgres_db") or "ha-metrics", user=sec.get("postgres_user") or "postgres", password=password, sslmode="prefer", connect_timeout=8)
    except Exception as exc:
        print("connect failed: %s" % type(exc).__name__, file=sys.stderr)
        return 1
    sql = "INSERT INTO public.telemetry (\"time\", site, metric, value, unit, device_class, state_class) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (\"time\", site, metric) DO UPDATE SET value=EXCLUDED.value, unit=EXCLUDED.unit, device_class=EXCLUDED.device_class, state_class=EXCLUDED.state_class"
    ensure = "CREATE TABLE IF NOT EXISTS public.telemetry (\"time\" timestamptz NOT NULL, site text NOT NULL, metric text NOT NULL, value double precision NOT NULL, unit text, device_class text, state_class text, PRIMARY KEY (\"time\", site, metric))"
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(ensure)
                for row in rows:
                    cur.execute(sql, (when, site, row["metric"], float(row["value"]), row.get("unit"), row.get("device_class"), row.get("state_class")))
        print("upserted %s at %s site=%s" % (len(rows), when.isoformat(), site))
        return 0
    except Exception as exc:
        print("write failed: %s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:180]), file=sys.stderr)
        return 1
    finally:
        conn.close()

if __name__ == "__main__":
    sys.exit(main())