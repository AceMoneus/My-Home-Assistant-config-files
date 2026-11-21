#!/usr/bin/env python3
"""Build the validated historical telemetry dataset and optionally upsert it.

Default is dry-run. This file does not import or write anything when it is
created or syntax-checked. PostgreSQL is opened only after --commit and only
after every validation gate has passed.

The validated snapshot ends at 2026-09-28 11:00:00 UTC. Later hours belong to
the production automation and are not part of this historical import.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

SQLITE_PATH = "/homeassistant/home-assistant_v2.db"
SECRETS_PATH = "/config/secrets.yaml"
SITE = "ha-server"
STOCKHOLM = ZoneInfo("Europe/Stockholm")
UTC = timezone.utc

# Inclusive last start_ts of the validated snapshot (2026-09-28 11:00:00Z).
VALIDATED_LAST_TS = int(datetime(2026, 9, 28, 11, 0, tzinfo=UTC).timestamp())
EXPECTED_ROWS = 230938
EXPECTED_MIN_COUNTS = {
    "power_l1_min": 11292,
    "power_l2_min": 11409,
    "power_l3_min": 11400,
}
EXPECTED_CLIMATE_OMITTED = 376
EXPECTED_NEGATIVE_MIN_DROPPED = {"power_l1_min": 117, "power_l2_min": 0, "power_l3_min": 9}
EXPECTED_PLATEAU_HOURS = 1834
EXPECTED_CATCHUP = 7
EXPECTED_GAPS = 17
SENTINEL_ENERGY = 281474976710.655
SENTINEL_POWER = (-32768.0, -8388608.0)

# 2025-01-15 17:00Z, previously validated, warning only.
KNOWN_HIGH_ENERGY = (int(datetime(2025, 1, 15, 17, 0, tzinfo=UTC).timestamp()), 16.715)

JUMP_LOCAL = (
    "2024-10-10 08:00",
    "2024-10-24 08:00",
    "2025-07-06 14:00",
    "2025-07-31 07:00",
    "2025-09-03 19:00",
    "2026-01-17 12:00",
    "2026-04-07 15:00",
)

CLIMATE = (
    ("temperature_inhouse", "sensor.temp_livingroom_temperatur", 1, "°C", "temperature", "measurement"),
    ("temperature_inhouse", "sensor.mt71_temperatur", 1, "°C", "temperature", "measurement"),
    ("humidity_inhouse", "sensor.temp_livingroom_luftfuktighet", 0, "%", "humidity", "measurement"),
    ("humidity_inhouse", "sensor.mt71_luftfuktighet", 0, "%", "humidity", "measurement"),
    ("temperature_outside", "sensor.motion_lyktstolpe_temperatur", 1, "°C", "temperature", "measurement"),
    ("temperature_outside", "sensor.mt72_temperatur", 1, "°C", "temperature", "measurement"),
    ("humidity_outside", "sensor.mt72_luftfuktighet", 0, "%", "humidity", "measurement"),
    ("basement_temperature", "sensor.temp_basement_temperatur", 1, "°C", "temperature", "measurement"),
    ("basement_temperature", "sensor.mt73_temperatur", 1, "°C", "temperature", "measurement"),
    ("basement_humidity", "sensor.temp_basement_luftfuktighet", 0, "%", "humidity", "measurement"),
    ("basement_humidity", "sensor.mt73_luftfuktighet", 0, "%", "humidity", "measurement"),
    ("bedroom_temperature", "sensor.temp_bedroom_temperatur", 1, "°C", "temperature", "measurement"),
    ("bedroom_temperature", "sensor.mt74_temperatur", 1, "°C", "temperature", "measurement"),
    ("bedroom_humidity", "sensor.temp_bedroom_luftfuktighet", 0, "%", "humidity", "measurement"),
    ("bedroom_humidity", "sensor.mt74_luftfuktighet", 0, "%", "humidity", "measurement"),
)

CLIMATE_FREEZE = {
    "sensor.temp_livingroom_temperatur": ("2026-05-25 05:00", "2026-05-28 23:00"),
    "sensor.temp_livingroom_luftfuktighet": ("2026-05-25 05:00", "2026-05-28 23:00"),
    "sensor.temp_bedroom_temperatur": ("2026-05-25 05:00", "2026-05-28 23:00"),
    "sensor.temp_bedroom_luftfuktighet": ("2026-05-25 05:00", "2026-05-28 23:00"),
    "sensor.temp_basement_temperatur": ("2025-12-31 22:00", "2026-01-01 03:00"),
    "sensor.temp_basement_luftfuktighet": ("2025-12-31 22:00", "2026-01-01 03:00"),
}

FRIENT_PHASES = (
    ("power_l1", "sensor.frient_electricity_effekt"),
    ("power_l2", "sensor.frient_electricity_effekt_fas_b"),
    ("power_l3", "sensor.frient_electricity_effekt_fas_c"),
)
FRIENT_TOTAL = "sensor.frient_electricity_total_effekt"
SLIMME_POWER = (
    ("total_power", "sensor.slimmelezer_momentary_active_import"),
    ("power_l1", "sensor.slimmelezer_momentary_active_import_phase_1"),
    ("power_l2", "sensor.slimmelezer_momentary_active_import_phase_2"),
    ("power_l3", "sensor.slimmelezer_momentary_active_import_phase_3"),
)
ENERGY_SERIES = (
    "sensor.cumulative_active_import",
    "sensor.frient_electricity_summering_av_leverans",
    "sensor.slimmelezer_cumulative_active_import",
)

UPSERT_SQL = """
INSERT INTO public.telemetry
    ("time", site, metric, value, unit, device_class, state_class)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ("time", site, metric) DO UPDATE SET
    value = EXCLUDED.value,
    unit = EXCLUDED.unit,
    device_class = EXCLUDED.device_class,
    state_class = EXCLUDED.state_class
"""


def round_half_up(value, digits):
    quant = Decimal("1") if digits == 0 else Decimal("0." + ("0" * (digits - 1)) + "1")
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def local_hour_set(start_text, end_text):
    start = datetime.strptime(start_text, "%Y-%m-%d %H:%M").replace(tzinfo=STOCKHOLM)
    end = datetime.strptime(end_text, "%Y-%m-%d %H:%M").replace(tzinfo=STOCKHOLM)
    hours = set()
    cur = start
    while cur <= end:
        hours.add(int(cur.timestamp()))
        cur += timedelta(hours=1)
    return hours


def utc_text(ts):
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def open_sqlite():
    uri = "file:" + SQLITE_PATH + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    value = conn.execute("PRAGMA query_only").fetchone()[0]
    if value != 1:
        conn.close()
        raise SystemExit("SQLite query_only is not 1; aborting without writes")
    return conn, value


def load_series(conn, statistic_id):
    meta = conn.execute(
        "SELECT id FROM statistics_meta WHERE statistic_id=?",
        (statistic_id,),
    ).fetchone()
    if meta is None:
        raise SystemExit(f"missing statistic_id {statistic_id}")
    rows = []
    for row in conn.execute(
        """
        SELECT start_ts, mean, min, max, state
        FROM statistics
        WHERE metadata_id=?
        ORDER BY start_ts
        """,
        (meta["id"],),
    ):
        ts = int(round(row["start_ts"]))
        if ts > VALIDATED_LAST_TS:
            continue
        rows.append(
            {
                "ts": ts,
                "mean": None if row["mean"] is None else float(row["mean"]),
                "min": None if row["min"] is None else float(row["min"]),
                "max": None if row["max"] is None else float(row["max"]),
                "state": None if row["state"] is None else float(row["state"]),
            }
        )
    return rows


def is_power_sentinel(value):
    if value is None:
        return True
    if value in SENTINEL_POWER or value <= -10000:
        return True
    return False


def is_energy_sentinel(value):
    if value is None or value < 0 or value >= 1e9:
        return True
    if abs(value - SENTINEL_ENERGY) < 0.001:
        return True
    return False


def add_row(bucket, stats, ts, metric, value, unit, device_class, state_class, source):
    bucket.append((ts, metric, value, unit, device_class, state_class, source))
    stats["by_metric"][metric].append((ts, value, source))


def build_dataset(conn):
    stats = {
        "climate_omitted": 0,
        "sentinel_phase_rows": 0,
        "sentinel_energy_states": 0,
        "negative_min_dropped": defaultdict(int),
        "plateau_hours": 0,
        "catchup_omitted": 0,
        "gaps": 0,
        "negative_energy_deltas": 0,
        "jumps_found": set(),
        "by_metric": defaultdict(list),
    }
    rows = []
    sentinel_block = local_hour_set("2025-04-07 16:00", "2025-05-12 18:00")
    if len(sentinel_block) != 843:
        raise SystemExit(f"sentinel window is {len(sentinel_block)} hours, expected 843")
    jump_ts = {next(iter(local_hour_set(text, text))) for text in JUMP_LOCAL}

    for metric, sid, digits, unit, device_class, state_class in CLIMATE:
        freeze = set()
        if sid in CLIMATE_FREEZE:
            freeze = local_hour_set(*CLIMATE_FREEZE[sid])
        for row in load_series(conn, sid):
            if row["mean"] is None:
                continue
            if row["ts"] in freeze:
                stats["climate_omitted"] += 1
                continue
            add_row(
                rows,
                stats,
                row["ts"],
                metric,
                round_half_up(row["mean"], digits),
                unit,
                device_class,
                state_class,
                sid,
            )

    phases = {}
    for base, sid in FRIENT_PHASES:
        by_ts = {}
        for row in load_series(conn, sid):
            if row["ts"] in sentinel_block or any(
                is_power_sentinel(row[field]) for field in ("mean", "min", "max")
            ):
                stats["sentinel_phase_rows"] += 1
                continue
            by_ts[row["ts"]] = row
            if row["mean"] is not None:
                add_row(rows, stats, row["ts"], base + "_mean", round_half_up(row["mean"], 0), "W", "power", "measurement", sid)
            if row["max"] is not None:
                add_row(rows, stats, row["ts"], base + "_max", round_half_up(row["max"], 0), "W", "power", "measurement", sid)
            if row["min"] is None:
                continue
            if row["min"] < 0:
                stats["negative_min_dropped"][base + "_min"] += 1
                continue
            add_row(rows, stats, row["ts"], base + "_min", round_half_up(row["min"], 0), "W", "power", "measurement", sid)
        phases[base] = by_ts

    total_hours = set()
    for row in load_series(conn, FRIENT_TOTAL):
        if row["ts"] in sentinel_block or any(is_power_sentinel(row[field]) for field in ("mean", "min", "max")):
            stats["sentinel_phase_rows"] += 1
            continue
        total_hours.add(row["ts"])
        for suffix, field in (("_mean", "mean"), ("_min", "min"), ("_max", "max")):
            if row[field] is None:
                continue
            add_row(
                rows,
                stats,
                row["ts"],
                "total_power" + suffix,
                round_half_up(row[field], 0),
                "W",
                "power",
                "measurement",
                FRIENT_TOTAL,
            )

    common = set(phases["power_l1"]) & set(phases["power_l2"]) & set(phases["power_l3"])
    for ts in sorted(common - total_hours):
        total_mean = phases["power_l1"][ts]["mean"] + phases["power_l2"][ts]["mean"] + phases["power_l3"][ts]["mean"]
        add_row(rows, stats, ts, "total_power_mean", round_half_up(total_mean, 0), "W", "power", "measurement", "phase_sum")

    for base, sid in SLIMME_POWER:
        for row in load_series(conn, sid):
            if row["mean"] is None or row["min"] is None or row["max"] is None:
                continue
            for suffix, field in (("_mean", "mean"), ("_min", "min"), ("_max", "max")):
                add_row(
                    rows,
                    stats,
                    row["ts"],
                    base + suffix,
                    round_half_up(row[field] * 1000, 0),
                    "W",
                    "power",
                    "measurement",
                    sid,
                )

    for sid in ENERGY_SERIES:
        valid = []
        for row in load_series(conn, sid):
            if is_energy_sentinel(row["state"]):
                stats["sentinel_energy_states"] += 1
                continue
            valid.append(row)
            add_row(
                rows,
                stats,
                row["ts"],
                "energy_imported_total",
                round_half_up(row["state"], 3),
                "kWh",
                "energy",
                "total_increasing",
                sid,
            )
        plateau = set()
        for index, row in enumerate(valid):
            if row["ts"] not in jump_ts or index == 0:
                continue
            frozen = valid[index - 1]["state"]
            cursor = index - 1
            while (
                cursor > 0
                and valid[cursor - 1]["state"] == frozen
                and valid[cursor]["ts"] - valid[cursor - 1]["ts"] == 3600
            ):
                plateau.add(valid[cursor]["ts"])
                cursor -= 1
        if len(valid) >= 6:
            end_state = valid[-1]["state"]
            cursor = len(valid) - 1
            while (
                cursor > 0
                and valid[cursor]["state"] == end_state
                and valid[cursor]["ts"] - valid[cursor - 1]["ts"] == 3600
                and valid[cursor - 1]["state"] == end_state
            ):
                cursor -= 1
            tail = valid[cursor + 1 :]
            if len(tail) >= 6:
                plateau.update(row["ts"] for row in tail)
        stats["plateau_hours"] += len(plateau)
        for index in range(1, len(valid)):
            previous, current = valid[index - 1], valid[index]
            gap = current["ts"] - previous["ts"]
            delta = current["state"] - previous["state"]
            if delta < -1e-9:
                stats["negative_energy_deltas"] += 1
                continue
            if gap != 3600:
                stats["gaps"] += 1
                continue
            if current["ts"] in jump_ts:
                stats["catchup_omitted"] += 1
                stats["jumps_found"].add(current["ts"])
                continue
            if current["ts"] in plateau:
                continue
            add_row(
                rows,
                stats,
                current["ts"],
                "energy_imported",
                round_half_up(delta, 3),
                "kWh",
                "energy",
                "total",
                sid,
            )
    stats["jump_ts"] = jump_ts
    return rows, stats


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def validate(rows, stats):
    errors = []
    warnings = []
    if len(rows) != EXPECTED_ROWS:
        errors.append(f"row count {len(rows)} != {EXPECTED_ROWS}")
    keys = defaultdict(list)
    high_energy = []
    known_high = 0
    for ts, metric, value, unit, device_class, state_class, source in rows:
        if ts is None or metric is None or value is None or not finite(value):
            errors.append(f"null or non-finite value at {ts} {metric}")
            continue
        keys[(ts, SITE, metric)].append(source)
        if metric.startswith("temperature_") and not (-40 <= value <= 60):
            errors.append(f"temperature out of range {utc_text(ts)} {metric} {value}")
        if metric.endswith("humidity") or metric.startswith("humidity_") or metric.startswith("basement_humidity") or metric.startswith("bedroom_humidity"):
            if "humidity" in metric and not (0 <= value <= 100):
                errors.append(f"humidity out of range {utc_text(ts)} {metric} {value}")
        if metric.endswith("_mean") or metric.endswith("_max"):
            if metric.startswith("power_") or metric.startswith("total_power"):
                if value < 0:
                    errors.append(f"negative power {metric} {utc_text(ts)} {value}")
        if metric.endswith("_min") and (metric.startswith("power_") or metric.startswith("total_power")):
            if value < 0:
                errors.append(f"negative power min {metric} {utc_text(ts)} {value}")
        if metric == "energy_imported":
            if value < 0:
                errors.append(f"negative energy_imported {utc_text(ts)} {value}")
            elif value > 15:
                high_energy.append((ts, value, source))
                if ts == KNOWN_HIGH_ENERGY[0] and abs(value - KNOWN_HIGH_ENERGY[1]) < 0.0005:
                    known_high += 1
    duplicates = {key: sources for key, sources in keys.items() if len(sources) > 1}
    if duplicates:
        errors.append(f"duplicates {len(duplicates)}; refusing to choose a winner")
    for metric, expected in EXPECTED_MIN_COUNTS.items():
        actual = len(stats["by_metric"].get(metric, []))
        if actual != expected:
            errors.append(f"{metric} count {actual} != {expected}")
        if any(value < 0 for _, value, _ in stats["by_metric"].get(metric, [])):
            errors.append(f"{metric} has a negative value")
    if stats["climate_omitted"] != EXPECTED_CLIMATE_OMITTED:
        errors.append(f"climate omitted {stats['climate_omitted']} != {EXPECTED_CLIMATE_OMITTED}")
    for metric, expected in EXPECTED_NEGATIVE_MIN_DROPPED.items():
        actual = stats["negative_min_dropped"][metric]
        if actual != expected:
            errors.append(f"negative min dropped {metric} {actual} != {expected}")
    if stats["plateau_hours"] != EXPECTED_PLATEAU_HOURS:
        errors.append(f"plateau hours {stats['plateau_hours']} != {EXPECTED_PLATEAU_HOURS}")
    if stats["catchup_omitted"] != EXPECTED_CATCHUP:
        errors.append(f"catch-up omitted {stats['catchup_omitted']} != {EXPECTED_CATCHUP}")
    if stats["gaps"] != EXPECTED_GAPS:
        errors.append(f"gaps {stats['gaps']} != {EXPECTED_GAPS}")
    if stats["negative_energy_deltas"]:
        errors.append(f"negative energy deltas {stats['negative_energy_deltas']}")
    if stats["jumps_found"] != stats["jump_ts"]:
        errors.append("one or more listed catch-up hours were not found")
    unexpected_high = [item for item in high_energy if not (item[0] == KNOWN_HIGH_ENERGY[0] and abs(item[1] - KNOWN_HIGH_ENERGY[1]) < 0.0005)]
    if unexpected_high:
        errors.append(f"unexpected energy_imported > 15 kWh: {len(unexpected_high)}")
        for ts, value, source in unexpected_high:
            errors.append(f"  {utc_text(ts)} {value} {source}")
    if known_high != 1:
        errors.append(f"known 16.715 kWh warning row count is {known_high}, expected 1")
    else:
        warnings.append("2025-01-15 17:00Z energy_imported=16.715 kWh kept as previously validated warning")
    return errors, warnings


def summarize(query_only, rows, stats, errors, warnings):
    print(f"SQLite query_only={query_only}")
    print(f"validated_last_utc={utc_text(VALIDATED_LAST_TS)}")
    print(f"total_rows={len(rows)}")
    keys = defaultdict(int)
    for ts, metric, *_rest in rows:
        keys[(ts, SITE, metric)] += 1
    print(f"duplicates={sum(1 for count in keys.values() if count > 1)}")
    print(f"filtered_sentinel_phase_rows={stats['sentinel_phase_rows']}")
    print(f"filtered_sentinel_energy_states={stats['sentinel_energy_states']}")
    print(f"filtered_climate_end_rows={stats['climate_omitted']}")
    print(
        "removed_negative_phase_min="
        + ",".join(f"{metric}:{stats['negative_min_dropped'][metric]}" for metric in EXPECTED_NEGATIVE_MIN_DROPPED)
    )
    print(f"removed_energy_plateau_hours={stats['plateau_hours']}")
    print(f"removed_energy_catchup_hours={stats['catchup_omitted']}")
    print(f"time_gaps_not_imported={stats['gaps']}")
    print("metrics:")
    for metric in sorted(stats["by_metric"]):
        items = stats["by_metric"][metric]
        values = [value for _, value, _ in items]
        times = [ts for ts, _, _ in items]
        print(
            f"  {metric}\tn={len(items)}\t{utc_text(min(times))}\t{utc_text(max(times))}"
            f"\tmin={min(values)}\tmax={max(values)}"
        )
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"  {warning}")
    if errors:
        print("VALIDATION FAILED")
        for error in errors:
            print(f"  {error}")
    else:
        print("VALIDATION PASSED")


def load_secrets():
    secrets = {}
    with open(SECRETS_PATH, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#") or line[:1].isspace() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            secrets[key.strip()] = value.strip().strip("'\"")
    return secrets


def commit_rows(rows):
    import psycopg2
    from psycopg2.extras import execute_batch

    secrets = load_secrets()
    password = secrets.get("postgres_password", "")
    if not password:
        print("missing postgres_password", file=sys.stderr)
        return 2
    payload = [
        (
            datetime.fromtimestamp(ts, UTC),
            SITE,
            metric,
            value,
            unit,
            device_class,
            state_class,
        )
        for ts, metric, value, unit, device_class, state_class, _source in rows
    ]
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
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            execute_batch(cur, UPSERT_SQL, payload, page_size=1000)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"write failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    counts = defaultdict(int)
    for _ts, metric, *_rest in rows:
        counts[metric] += 1
    print(f"upsert_attempts={len(payload)}")
    for metric in sorted(counts):
        print(f"  {metric}\t{counts[metric]}")
    print("commit_status=committed")
    print("COMMIT SUCCESSFUL")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Validate or import historical telemetry. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Build and validate only. This is the default.")
    parser.add_argument("--commit", action="store_true", help="Upsert only after validation passes.")
    args = parser.parse_args()
    if args.commit and args.dry_run:
        parser.error("use either --dry-run or --commit, not both")
    return args


def main():
    args = parse_args()
    conn, query_only = open_sqlite()
    try:
        rows, stats = build_dataset(conn)
    finally:
        conn.close()
    errors, warnings = validate(rows, stats)
    summarize(query_only, rows, stats, errors, warnings)
    if not args.commit:
        print("DRY RUN - NO POSTGRESQL WRITES")
        return 2 if errors else 0
    if errors:
        print("ABORTED - NO POSTGRESQL WRITES")
        return 2
    return commit_rows(rows)


if __name__ == "__main__":
    sys.exit(main())
