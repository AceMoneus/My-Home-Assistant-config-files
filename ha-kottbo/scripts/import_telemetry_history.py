#!/usr/bin/env python3
"""Historical telemetry builder for ha-kottbo.

Default mode reads Home Assistant long-term statistics read-only, validates
the in-memory dataset, and does not connect to PostgreSQL.

PostgreSQL upserts run only when --commit is passed, and only after the
existing validation has passed. That path does not change schema and does
not remove rows.

SQLite is opened read-only. Only statistics_meta and statistics are read.
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

SQLITE_URI = "file:/homeassistant/home-assistant_v2.db?mode=ro"
SITE = "ha-kottbo"
STOCKHOLM = ZoneInfo("Europe/Stockholm")
UTC = timezone.utc

# Observed simultaneous freeze hours, inclusive start_ts.
# July nominal local end 2026-07-17 19:00 CEST is the thaw/catch-up hour and
# is not included. August/September matches the stated local bounds on both ends.
FREEZE_BLOCKS = (
    {
        "name": "system_freeze_2026_07",
        "start_ts": 1783875600,  # 2026-07-12 17:00 UTC / 19:00 CEST
        "end_ts": 1784304000,  # 2026-07-17 16:00 UTC / 18:00 CEST
        "nominal_local_end_excluded_ts": 1784307600,
    },
    {
        "name": "system_freeze_2026_08",
        "start_ts": 1788145200,  # 2026-08-31 03:00 UTC / 05:00 CEST
        "end_ts": 1788332400,  # 2026-09-02 07:00 UTC / 09:00 CEST
        "nominal_local_end_excluded_ts": None,
    },
)

# Documented kitchen stale blocks, inclusive. Not a general 22 C / 80 % rule.
KITCHEN_STALE_BLOCKS = (
    (1775887200, 1776402000),  # 2026-04-11 08:00 .. 2026-04-17 07:00 CEST
    (1776513600, 1777168800),  # 2026-04-18 14:00 .. 2026-04-26 04:00 CEST
    (1777730400, 1777996800),  # 2026-05-02 16:00 .. 2026-05-05 18:00 CEST
)

# Catch-up hours already identified. energy_imported is omitted for the hour
# and for the immediately preceding exact-state plateau. The cumulative state
# series is not rewritten.
EXPECTED_CATCHUPS = (
    1756432800,  # 2025-08-29 04:00 CEST
    1784307600,  # 2026-07-17 19:00 CEST
    1788336000,  # 2026-09-02 10:00 CEST
)

SOURCES = {
    "temperature_inhouse": "sensor.temp_kitchen_temperature",
    "humidity_inhouse": "sensor.temp_kitchen_humidity",
    "temperature_outside": "sensor.temp_outside_temperatur",
    "humidity_outside": "sensor.temp_outside_luftfuktighet",
    "basement_temperature": "sensor.temp_basement_temperatur",
    "basement_humidity": "sensor.temp_basement_luftfuktighet",
    "bedroom_temperature": "sensor.temp_bedroom_temperatur",
    "bedroom_humidity": "sensor.temp_bedroom_luftfuktighet",
    "ground_dry_side_temperature": "sensor.ground_dry_side_temperature",
    "ground_dry_side_humidity": "sensor.ground_dry_side_humidity",
    "ground_wet_side_temperature": "sensor.ground_wet_side_temperature",
    "ground_wet_side_humidity": "sensor.ground_wet_side_humidity",
    "power_l1": "sensor.frient_energy_effekt_fas_a",
    "power_l2": "sensor.frient_energy_effekt_fas_b",
    "power_l3": "sensor.frient_energy_effekt_fas_c",
    "total_power": "sensor.frient_energy_total_effekt",
    "energy_imported_total": "sensor.frient_energy_summering_av_leverans",
}

CLIMATE_METRICS = (
    "temperature_inhouse",
    "humidity_inhouse",
    "temperature_outside",
    "humidity_outside",
    "basement_temperature",
    "basement_humidity",
    "bedroom_temperature",
    "bedroom_humidity",
    "ground_dry_side_temperature",
    "ground_dry_side_humidity",
    "ground_wet_side_temperature",
    "ground_wet_side_humidity",
)
POWER_PHASES = ("power_l1", "power_l2", "power_l3")
EXPECTED_METRICS = (
    "power_l1_mean",
    "power_l1_min",
    "power_l1_max",
    "power_l2_mean",
    "power_l2_min",
    "power_l2_max",
    "power_l3_mean",
    "power_l3_min",
    "power_l3_max",
    "total_power_mean",
    "total_power_min",
    "total_power_max",
    "energy_imported",
    "energy_imported_total",
    *CLIMATE_METRICS,
)

META = {
    "temperature": ("°C", "temperature", "measurement", 1),
    "humidity": ("%", "humidity", "measurement", 0),
    "power": ("W", "power", "measurement", 0),
    "energy_imported": ("kWh", "energy", "measurement", 3),
    "energy_imported_total": ("kWh", "energy", "total_increasing", 3),
}


class ValidationFailure(Exception):
    pass


def fail(message):
    raise ValidationFailure(message)


def round_half_up(value, digits):
    quant = Decimal("1") if digits == 0 else Decimal("1").scaleb(-digits)
    rounded = Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
    return int(rounded) if digits == 0 else float(rounded)


def utc_label(ts):
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def local_label(ts):
    return datetime.fromtimestamp(int(ts), STOCKHOLM).strftime("%Y-%m-%d %H:%M:%S %Z")


def finite(value):
    return value is not None and not (isinstance(value, float) and (math.isnan(value) or math.isinf(value)))


def in_ranges(ts, ranges):
    return any(start <= ts <= end for start, end in ranges)


def open_sqlite():
    conn = sqlite3.connect(SQLITE_URI, uri=True, timeout=60)
    conn.execute("PRAGMA query_only = ON")
    status = conn.execute("PRAGMA query_only").fetchone()[0]
    if status != 1:
        fail("PRAGMA query_only is %s, expected 1" % status)
    return conn, status


def load_series(conn):
    statistic_ids = tuple(SOURCES.values())
    placeholders = ",".join("?" * len(statistic_ids))
    meta_rows = conn.execute(
        "SELECT id, statistic_id FROM statistics_meta WHERE statistic_id IN (%s)" % placeholders,
        statistic_ids,
    ).fetchall()
    found = {row[1]: row[0] for row in meta_rows}
    missing = [sid for sid in statistic_ids if sid not in found]
    if missing:
        fail("missing statistics_meta rows: %s" % ", ".join(missing))
    ids = list(found.values())
    placeholders = ",".join("?" * len(ids))
    raw = conn.execute(
        """
        SELECT metadata_id, start_ts, mean, min, max, state
        FROM statistics
        WHERE metadata_id IN (%s)
        ORDER BY metadata_id, start_ts
        """
        % placeholders,
        ids,
    ).fetchall()
    by_id = defaultdict(list)
    for metadata_id, start_ts, mean, minimum, maximum, state in raw:
        ts = float(start_ts)
        if abs(ts - round(ts)) > 1e-6 or int(round(ts)) % 3600 != 0:
            fail("non-hour start_ts %s for metadata_id %s" % (start_ts, metadata_id))
        by_id[metadata_id].append((int(round(ts)), mean, minimum, maximum, state))
    series = {}
    for metric, statistic_id in SOURCES.items():
        points = by_id[found[statistic_id]]
        if not points:
            fail("no statistics rows for %s" % statistic_id)
        series[metric] = {
            "statistic_id": statistic_id,
            "metadata_id": found[statistic_id],
            "points": points,
            "by_ts": {item[0]: item for item in points},
        }
    return series


def hour_set(start_ts, end_ts):
    return set(range(start_ts, end_ts + 3600, 3600))


def freeze_hours():
    hours = set()
    for block in FREEZE_BLOCKS:
        hours |= hour_set(block["start_ts"], block["end_ts"])
    return hours


def detect_kitchen_stale(series):
    temp = series["temperature_inhouse"]["by_ts"]
    humid = series["humidity_inhouse"]["by_ts"]
    stale = []
    for ts, row in temp.items():
        other = humid.get(ts)
        if other is None:
            continue
        t_mean, t_min, t_max = row[1], row[2], row[3]
        h_mean, h_min, h_max = other[1], other[2], other[3]
        if (
            finite(t_mean)
            and t_mean == t_min == t_max == 22.0
            and finite(h_mean)
            and h_mean == h_min == h_max == 80.0
        ):
            stale.append(ts)
    stale.sort()
    blocks = []
    if stale:
        start = prev = stale[0]
        for ts in stale[1:]:
            if ts == prev + 3600:
                prev = ts
            else:
                blocks.append((start, prev))
                start = prev = ts
        blocks.append((start, prev))
    expected = set(KITCHEN_STALE_BLOCKS)
    if not expected.issubset(set(blocks)):
        fail(
            "documented kitchen stale blocks are missing: found %s expected %s"
            % (blocks, KITCHEN_STALE_BLOCKS)
        )
    extra = [block for block in blocks if block not in expected]
    documented = [block for block in blocks if block in expected]
    documented_hours = set()
    for start, end in documented:
        documented_hours |= hour_set(start, end)
    return documented_hours, documented, extra


def detect_catchups(series):
    points = series["energy_imported_total"]["points"]
    decreases = []
    for (t0, _, _, _, s0), (t1, _, _, _, s1) in zip(points, points[1:]):
        if not finite(s0) or not finite(s1):
            fail("energy state is not finite at %s or %s" % (t0, t1))
        if s1 < s0:
            decreases.append((t0, t1, s0, s1, s1 - s0))
    if decreases:
        fail("energy state decreased: %s" % decreases[:5])

    runs = []
    run_start = 0
    for index in range(1, len(points) + 1):
        contiguous = False
        if index < len(points):
            contiguous = (
                points[index][0] - points[index - 1][0] == 3600
                and points[index][4] == points[index - 1][4]
            )
        if contiguous:
            continue
        start = points[run_start]
        end = points[index - 1]
        hours = int((end[0] - start[0]) / 3600) + 1
        if hours >= 24 and start[4] == end[4]:
            runs.append((start[0], end[0], hours, start[4]))
        run_start = index

    catchups = []
    by_ts = series["energy_imported_total"]["by_ts"]
    for start, end, hours, state in runs:
        nxt = end + 3600
        row = by_ts.get(nxt)
        if row is None:
            continue
        delta = row[4] - state
        if delta > 10:
            catchups.append(
                {
                    "plateau_start": start,
                    "plateau_end": end,
                    "plateau_hours": hours,
                    "catchup_ts": nxt,
                    "delta": delta,
                    "state_before": state,
                    "state_after": row[4],
                }
            )
    found = tuple(item["catchup_ts"] for item in catchups)
    if found != EXPECTED_CATCHUPS:
        fail("catch-up timestamps %s do not match documented %s" % (found, EXPECTED_CATCHUPS))
    plateau_hours = set()
    for item in catchups:
        plateau_hours |= hour_set(item["plateau_start"], item["plateau_end"])
    return catchups, plateau_hours


def metric_kind(metric):
    if metric.startswith("power_") or metric.startswith("total_power"):
        return "power"
    if metric == "energy_imported":
        return "energy_imported"
    if metric == "energy_imported_total":
        return "energy_imported_total"
    if "humidity" in metric:
        return "humidity"
    if "temperature" in metric:
        return "temperature"
    fail("unknown metric kind for %s" % metric)


def check_value(metric, value):
    if not finite(value):
        fail("%s has non-finite value %s" % (metric, value))
    kind = metric_kind(metric)
    if kind == "temperature" and not -40 <= value <= 60:
        fail("%s temperature out of range: %s" % (metric, value))
    if kind == "humidity" and not 0 <= value <= 100:
        fail("%s humidity out of range: %s" % (metric, value))
    if kind == "power" and value < 0:
        fail("%s negative power: %s" % (metric, value))


def add_row(rows, seen, ts, metric, value, warnings):
    check_value(metric, value)
    kind = metric_kind(metric)
    unit, device_class, state_class, digits = META[kind]
    rounded = round_half_up(value, digits)
    check_value(metric, rounded)
    key = (ts, SITE, metric)
    if key in seen:
        fail("duplicate key %s" % (key,))
    seen.add(key)
    rows.append(
        {
            "time": datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "start_ts": ts,
            "site": SITE,
            "metric": metric,
            "value": rounded,
            "unit": unit,
            "device_class": device_class,
            "state_class": state_class,
        }
    )


def missing_hours(points):
    timestamps = [item[0] for item in points]
    first, last = timestamps[0], timestamps[-1]
    have = set(timestamps)
    missing = []
    ts = first
    while ts <= last:
        if ts not in have:
            missing.append(ts)
        ts += 3600
    return missing


def build(series):
    frozen = freeze_hours()
    kitchen_hours, kitchen_blocks, kitchen_extra = detect_kitchen_stale(series)
    catchups, plateau_hours = detect_catchups(series)
    catchup_hours = {item["catchup_ts"] for item in catchups}
    rows = []
    seen = set()
    warnings = []
    freeze_removed = defaultdict(int)
    kitchen_removed = defaultdict(int)

    def note_freeze(metric):
        freeze_removed[metric] += 1

    for metric in CLIMATE_METRICS:
        for ts, mean, minimum, maximum, _state in series[metric]["points"]:
            if not finite(mean):
                fail("%s mean is not finite at %s" % (metric, ts))
            if ts in frozen:
                note_freeze(metric)
                continue
            if metric in ("temperature_inhouse", "humidity_inhouse") and ts in kitchen_hours:
                kitchen_removed[metric] += 1
                continue
            add_row(rows, seen, ts, metric, mean, warnings)

    for phase in POWER_PHASES:
        for ts, mean, minimum, maximum, _state in series[phase]["points"]:
            for suffix, raw in (("mean", mean), ("min", minimum), ("max", maximum)):
                metric = "%s_%s" % (phase, suffix)
                if not finite(raw):
                    fail("%s is not finite at %s" % (metric, ts))
                if ts in frozen:
                    note_freeze(metric)
                    continue
                add_row(rows, seen, ts, metric, raw, warnings)

    total_points = series["total_power"]["points"]
    total_first = total_points[0][0]
    for ts, mean, minimum, maximum, _state in total_points:
        for suffix, raw in (("mean", mean), ("min", minimum), ("max", maximum)):
            metric = "total_power_%s" % suffix
            if not finite(raw):
                fail("%s is not finite at %s" % (metric, ts))
            if ts in frozen:
                note_freeze(metric)
                continue
            add_row(rows, seen, ts, metric, raw, warnings)

    phase_maps = {phase: series[phase]["by_ts"] for phase in POWER_PHASES}
    phase_timestamps = [set(series[phase]["by_ts"]) for phase in POWER_PHASES]
    common_before = set.intersection(*phase_timestamps)
    common_before = {ts for ts in common_before if ts < total_first}
    reconstructed = 0
    for ts in sorted(common_before):
        if ts in frozen:
            note_freeze("total_power_mean")
            continue
        values = [phase_maps[phase][ts][1] for phase in POWER_PHASES]
        if not all(finite(value) for value in values):
            fail("phase mean missing while reconstructing total_power_mean at %s" % ts)
        add_row(rows, seen, ts, "total_power_mean", sum(values), warnings)
        reconstructed += 1

    early_min_max = sum(
        1
        for row in rows
        if row["metric"] in ("total_power_min", "total_power_max") and row["start_ts"] < total_first
    )
    if early_min_max != 0:
        fail("total_power_min/max rows before original series start: %s" % early_min_max)

    energy = series["energy_imported_total"]
    for ts, _mean, _minimum, _maximum, state in energy["points"]:
        if not finite(state):
            fail("energy state not finite at %s" % ts)
        if ts in frozen:
            note_freeze("energy_imported_total")
            continue
        add_row(rows, seen, ts, "energy_imported_total", state, warnings)

    energy_imported_omitted_plateau = 0
    catchup_removed = []
    by_ts = energy["by_ts"]
    points = energy["points"]
    for prev, curr in zip(points, points[1:]):
        if curr[0] - prev[0] != 3600:
            continue
        delta = curr[4] - prev[4]
        ts = curr[0]
        if ts in frozen:
            note_freeze("energy_imported")
            continue
        if ts in catchup_hours:
            catchup_removed.append((ts, delta))
            continue
        if ts in plateau_hours:
            energy_imported_omitted_plateau += 1
            continue
        add_row(rows, seen, ts, "energy_imported", delta, warnings)

    if len(catchup_removed) != 3:
        fail("expected 3 removed catch-up deltas, found %s" % len(catchup_removed))

    # Overlap check: phase-sum mean versus original total mean. Report only.
    overlap = []
    total_map = series["total_power"]["by_ts"]
    for ts, row in total_map.items():
        if not all(ts in phase_maps[phase] for phase in POWER_PHASES):
            continue
        phase_sum = sum(phase_maps[phase][ts][1] for phase in POWER_PHASES)
        if finite(phase_sum) and finite(row[1]):
            overlap.append(abs(row[1] - phase_sum))
    overlap.sort()
    overlap_stats = None
    if overlap:
        within = lambda limit: 100.0 * sum(value <= limit for value in overlap) / len(overlap)
        overlap_stats = {
            "n": len(overlap),
            "mad": sum(overlap) / len(overlap),
            "median": overlap[len(overlap) // 2],
            "max": overlap[-1],
            "within_1": within(1),
            "within_5": within(5),
            "within_10": within(10),
        }
        if overlap_stats["within_5"] < 99.0:
            warnings.append(
                "phase_sum_mean agreement weakened: %.2f%% within 5 W" % overlap_stats["within_5"]
            )

    # July 19:00 CEST is outside the shared freeze. Some climate series are still flat.
    thaw = 1784307600
    still_flat = []
    for metric in CLIMATE_METRICS:
        row = series[metric]["by_ts"].get(thaw)
        if row and finite(row[1]) and row[1] == row[2] == row[3]:
            still_flat.append(metric)
    if still_flat:
        warnings.append(
            "2026-07-17 19:00 CEST (%s) is not in the shared freeze filter because power and kitchen temperature thawed. Still min=mean=max: %s"
            % (utc_label(thaw), ", ".join(still_flat))
        )
    for start, end in kitchen_extra:
        warnings.append(
            "kitchen 22.0/80.0 block not removed (not one of the three documented long blocks): %s .. %s (%s hours)"
            % (utc_label(start), utc_label(end), int((end - start) / 3600) + 1)
        )

    return {
        "rows": rows,
        "freeze_removed": freeze_removed,
        "kitchen_removed": kitchen_removed,
        "kitchen_blocks": kitchen_blocks,
        "catchups": catchups,
        "catchup_removed": catchup_removed,
        "plateau_hours": plateau_hours,
        "energy_imported_omitted_plateau": energy_imported_omitted_plateau,
        "reconstructed": reconstructed,
        "early_min_max": early_min_max,
        "total_first": total_first,
        "overlap_stats": overlap_stats,
        "warnings": warnings,
        "frozen": frozen,
    }


def dst_check(rows):
    by_metric = defaultdict(list)
    for row in rows:
        by_metric[row["metric"]].append(row["start_ts"])
    local_collisions = 0
    for metric, stamps in by_metric.items():
        if len(stamps) != len(set(stamps)):
            fail("UTC key collision in %s" % metric)
        labels = defaultdict(list)
        for ts in stamps:
            labels[local_label(ts)[:16]].append(ts)
        for label, group in labels.items():
            if len(group) > 1:
                local_collisions += 1
                group = sorted(group)
                if any(b - a != 3600 for a, b in zip(group, group[1:])):
                    fail("local-hour collision is not a DST pair for %s %s" % (metric, label))
    return local_collisions


def natural_gaps(series):
    total = 0
    per_source = {}
    for key, item in series.items():
        missing = missing_hours(item["points"])
        per_source[item["statistic_id"]] = len(missing)
        total += len(missing)
    return total, per_source


def summarize(query_only, series, built):
    rows = built["rows"]
    metrics = sorted({row["metric"] for row in rows})
    if tuple(sorted(EXPECTED_METRICS)) != tuple(metrics):
        fail("metric set mismatch: %s" % metrics)
    if len(metrics) != 26:
        fail("expected 26 metrics, found %s" % len(metrics))
    if any(row["metric"].startswith("attic") or "vind" in row["metric"] for row in rows):
        fail("attic metric was produced")
    dst_collisions = dst_check(rows)
    gap_total, gap_sources = natural_gaps(series)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["metric"]].append(row)
    last_ts = max(row["start_ts"] for row in rows)
    lines = []
    lines.append("mode: dry-run")
    lines.append("postgresql_write_path: gated")
    lines.append("sqlite_uri: %s" % SQLITE_URI)
    lines.append("sqlite_query_only: %s" % query_only)
    lines.append("validated_last_utc: %s" % utc_label(last_ts))
    lines.append("total_rows: %s" % len(rows))
    lines.append("duplicates: 0")
    lines.append("metrics: %s" % len(metrics))
    lines.append("system_freeze_unique_hours: %s" % len(built["frozen"]))
    lines.append("system_freeze_rows_removed: %s" % sum(built["freeze_removed"].values()))
    lines.append("kitchen_stale_rows_removed: %s" % sum(built["kitchen_removed"].values()))
    plateau_in_freeze = len(built["plateau_hours"] & built["frozen"])
    lines.append("energy_plateau_hours_without_energy_imported: %s" % len(built["plateau_hours"]))
    lines.append("energy_plateau_hours_also_inside_freeze: %s" % plateau_in_freeze)
    lines.append(
        "energy_imported_rows_omitted_for_plateau_outside_freeze: %s"
        % built["energy_imported_omitted_plateau"]
    )
    lines.append("catchup_deltas_removed: %s" % len(built["catchup_removed"]))
    lines.append("natural_missing_hours_not_filled: %s" % gap_total)
    lines.append("energy_decreases: 0")
    lines.append("reconstructed_total_power_mean_before_original: %s" % built["reconstructed"])
    lines.append("total_power_min_max_before_original: %s" % built["early_min_max"])
    lines.append("dst_local_label_collisions_kept_as_distinct_utc: %s" % dst_collisions)
    lines.append("energy_imported_total_compensated: no")
    lines.append("")
    lines.append("freeze_blocks:")
    for block in FREEZE_BLOCKS:
        count = int((block["end_ts"] - block["start_ts"]) / 3600) + 1
        lines.append(
            "  %s start_ts=%s end_ts=%s hours=%s utc=%s .. %s local=%s .. %s"
            % (
                block["name"],
                block["start_ts"],
                block["end_ts"],
                count,
                utc_label(block["start_ts"]),
                utc_label(block["end_ts"]),
                local_label(block["start_ts"]),
                local_label(block["end_ts"]),
            )
        )
        lines.append("    last_filtered_start_ts: %s" % block["end_ts"])
        if block["nominal_local_end_excluded_ts"] is not None:
            excluded = block["nominal_local_end_excluded_ts"]
            lines.append(
                "    nominal_end_not_filtered: %s %s / %s"
                % (excluded, utc_label(excluded), local_label(excluded))
            )
    lines.append("freeze_rows_removed_per_metric:")
    for metric in EXPECTED_METRICS:
        lines.append("  %s: %s" % (metric, built["freeze_removed"].get(metric, 0)))
    lines.append("kitchen_stale_blocks:")
    for start, end in built["kitchen_blocks"]:
        hours = int((end - start) / 3600) + 1
        lines.append(
            "  start_ts=%s end_ts=%s hours=%s utc=%s .. %s local=%s .. %s"
            % (
                start,
                end,
                hours,
                utc_label(start),
                utc_label(end),
                local_label(start),
                local_label(end),
            )
        )
    lines.append("catchups:")
    for item in built["catchups"]:
        lines.append(
            "  catchup_ts=%s %s / %s delta=%.3f plateau_hours=%s plateau=%s .. %s"
            % (
                item["catchup_ts"],
                utc_label(item["catchup_ts"]),
                local_label(item["catchup_ts"]),
                item["delta"],
                item["plateau_hours"],
                utc_label(item["plateau_start"]),
                utc_label(item["plateau_end"]),
            )
        )
    if built["overlap_stats"]:
        stats = built["overlap_stats"]
        lines.append(
            "phase_sum_mean_overlap: n=%s mad=%.4f median=%.4f max=%.4f within_1=%.2f%% within_5=%.2f%% within_10=%.2f%%"
            % (
                stats["n"],
                stats["mad"],
                stats["median"],
                stats["max"],
                stats["within_1"],
                stats["within_5"],
                stats["within_10"],
            )
        )
    lines.append("natural_missing_hours_by_source:")
    for statistic_id, count in sorted(gap_sources.items()):
        lines.append("  %s: %s" % (statistic_id, count))
    lines.append("metrics:")
    for metric in EXPECTED_METRICS:
        items = grouped[metric]
        values = [item["value"] for item in items]
        stamps = [item["start_ts"] for item in items]
        lines.append(
            "  %s n=%s first=%s last=%s min=%s max=%s"
            % (
                metric,
                len(items),
                utc_label(min(stamps)),
                utc_label(max(stamps)),
                min(values),
                max(values),
            )
        )
    if built["warnings"]:
        lines.append("warnings:")
        for warning in built["warnings"]:
            lines.append("  %s" % warning)
    else:
        lines.append("warnings: none")
    lines.append("VALIDATION PASSED")
    return "\n".join(lines) + "\n"


BATCH_SIZE = 2000
PG_DATABASE = "ha-metrics"
PG_TABLE = "public.telemetry"
UPSERT_SQL = (
    "INSERT INTO public.telemetry "
    '("time", site, metric, value, unit, device_class, state_class) '
    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
    'ON CONFLICT ("time", site, metric) DO UPDATE SET '
    "value = EXCLUDED.value, "
    "unit = EXCLUDED.unit, "
    "device_class = EXCLUDED.device_class, "
    "state_class = EXCLUDED.state_class"
)


class CommitError(Exception):
    pass


def parse_mode(argv):
    if not argv:
        return "dry-run"
    if argv == ["--commit"]:
        return "commit"
    return None


def redact(message, secret):
    text = "" if message is None else str(message)
    if secret:
        text = text.replace(secret, "[redacted]")
    return text.splitlines()[0][:300] if text else ""


def load_pg_secrets():
    path = None
    for candidate in ("/homeassistant/secrets.yaml", "/config/secrets.yaml"):
        if os.path.isfile(candidate):
            path = candidate
            break
    if path is None:
        raise CommitError("postgres secrets file not found")
    found = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line[0] in "# " or ":" not in line:
                continue
            key, value = line.split(":", 1)
            found[key.strip()] = value.strip().strip("'\"")
    required = ("postgres_host", "postgres_db", "postgres_user", "postgres_password")
    missing = [key for key in required if not found.get(key)]
    if missing:
        raise CommitError("missing postgres secret names: %s" % ", ".join(missing))
    if found["postgres_db"] != PG_DATABASE:
        raise CommitError("postgres_db is not %s" % PG_DATABASE)
    return found


def connect_postgres(secrets):
    try:
        import psycopg2
    except ImportError as exc:
        raise CommitError("psycopg2 is not available") from exc
    try:
        conn = psycopg2.connect(
            host=secrets["postgres_host"],
            port=5432,
            dbname=PG_DATABASE,
            user=secrets["postgres_user"],
            password=secrets["postgres_password"],
            sslmode="prefer",
            connect_timeout=15,
            application_name="import_telemetry_history",
            options="-c statement_timeout=1800000",
        )
    except Exception as exc:
        raise CommitError("connect failed: %s" % redact(exc, secrets["postgres_password"])) from exc
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            current = cur.fetchone()[0]
        if current != PG_DATABASE:
            raise CommitError("connected database is not %s" % PG_DATABASE)
    except CommitError:
        conn.rollback()
        conn.close()
        raise
    except Exception as exc:
        conn.rollback()
        conn.close()
        raise CommitError("database check failed: %s" % redact(exc, secrets["postgres_password"])) from exc
    return conn


def dataset_params(rows):
    if not rows:
        raise CommitError("refusing an empty dataset")
    metrics = {row["metric"] for row in rows}
    if metrics != set(EXPECTED_METRICS):
        raise CommitError("refusing a dataset that is not the validated 26 metrics")
    params = []
    seen = set()
    for row in rows:
        if row["site"] != SITE:
            raise CommitError("refusing a row whose site is not %s" % SITE)
        key = (row["start_ts"], row["site"], row["metric"])
        if key in seen:
            raise CommitError("refusing a dataset with duplicate keys")
        seen.add(key)
        params.append(
            (
                datetime.fromtimestamp(int(row["start_ts"]), UTC),
                row["site"],
                row["metric"],
                float(row["value"]),
                row["unit"],
                row["device_class"],
                row["state_class"],
            )
        )
    return params


def upsert_batches(cur, params):
    from psycopg2.extras import execute_batch

    affected = 0
    reliable = True
    for offset in range(0, len(params), BATCH_SIZE):
        batch = params[offset : offset + BATCH_SIZE]
        execute_batch(cur, UPSERT_SQL, batch, page_size=BATCH_SIZE)
        if cur.rowcount is None or cur.rowcount < 0:
            reliable = False
        else:
            affected += cur.rowcount
    return affected if reliable else None


def as_utc_ts(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(round(value.timestamp()))


def values_equal(left, right):
    return abs(float(left) - float(right)) <= 1e-9


def fetch_site_rows(cur):
    cur.execute(
        'SELECT "time", metric, value, unit, device_class, state_class '
        "FROM public.telemetry WHERE site = %s",
        (SITE,),
    )
    rows = []
    duplicate_keys = 0
    seen = set()
    for when, metric, value, unit, device_class, state_class in cur.fetchall():
        key = (as_utc_ts(when), metric)
        if key in seen:
            duplicate_keys += 1
        seen.add(key)
        rows.append(
            {
                "start_ts": key[0],
                "metric": metric,
                "value": value,
                "unit": unit,
                "device_class": device_class,
                "state_class": state_class,
            }
        )
    return rows, duplicate_keys


def sql_duplicate_count(cur):
    cur.execute(
        "SELECT COUNT(*) FROM ("
        'SELECT "time", site, metric FROM public.telemetry '
        "WHERE site = %s "
        'GROUP BY "time", site, metric '
        "HAVING COUNT(*) > 1"
        ") AS duplicate_keys",
        (SITE,),
    )
    return int(cur.fetchone()[0])


def compare_dataset(dataset, fetched):
    expected = {(row["start_ts"], row["metric"]): row for row in dataset}
    fetched_by_key = {(row["start_ts"], row["metric"]): row for row in fetched}
    missing = []
    mismatched = []
    for key, row in expected.items():
        found = fetched_by_key.get(key)
        if found is None:
            missing.append(key)
            continue
        if (
            not values_equal(found["value"], row["value"])
            or found["unit"] != row["unit"]
            or found["device_class"] != row["device_class"]
            or found["state_class"] != row["state_class"]
        ):
            mismatched.append(key)
    extras = [row for key, row in fetched_by_key.items() if key not in expected]
    return missing, mismatched, extras


def metric_stats(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["metric"]].append(row)
    return grouped


def sample_keys(dataset):
    by_metric = metric_stats(dataset)
    chosen = []
    for metric in EXPECTED_METRICS:
        items = by_metric.get(metric) or []
        if not items:
            continue
        stamps = sorted(item["start_ts"] for item in items)
        chosen.append((stamps[0], metric))
        chosen.append((stamps[len(stamps) // 2], metric))
        chosen.append((stamps[-1], metric))
    return chosen[:12]


def format_commit_report(host, dataset, fetched, duplicate_sql, affected, extras, missing, mismatched):
    last_ts = max(row["start_ts"] for row in dataset)
    historical_keys = {(row["start_ts"], row["metric"]) for row in dataset}
    historical_fetched = [row for row in fetched if (row["start_ts"], row["metric"]) in historical_keys]
    grouped = metric_stats(historical_fetched)
    site_metrics = sorted({row["metric"] for row in fetched})
    historical_metrics = sorted({row["metric"] for row in historical_fetched})
    extra_metrics = sorted({row["metric"] for row in extras})
    ok = (
        duplicate_sql == 0
        and not missing
        and not mismatched
        and historical_metrics == sorted(EXPECTED_METRICS)
        and len(historical_fetched) == len(dataset)
    )
    lines = [
        "mode: commit",
        "validated_last_utc: %s" % utc_label(last_ts),
        "historical_rows: %s" % len(dataset),
        "batch_size: %s" % BATCH_SIZE,
        "postgresql_target: database=%s table=%s host=%s" % (PG_DATABASE, PG_TABLE, host),
        "site: %s" % SITE,
        "rows_processed: %s" % len(dataset),
    ]
    if affected is None:
        lines.append("rows_affected: unavailable")
    else:
        lines.append("rows_affected_driver: %s" % affected)
    lines.append("rows_affected_insert_vs_update: unavailable")
    lines.append("duplicates_after_import: %s" % duplicate_sql)
    lines.append("distinct_metrics_historical: %s" % len(historical_metrics))
    lines.append("distinct_metrics_site: %s" % len(site_metrics))
    lines.append("site_row_count: %s" % len(fetched))
    lines.append("live_rows_outside_historical_dataset: %s" % len(extras))
    if extra_metrics:
        lines.append("live_extra_metrics: %s" % ", ".join(extra_metrics))
    lines.append("historical_keys_missing: %s" % len(missing))
    lines.append("historical_keys_mismatched: %s" % len(mismatched))
    lines.append("metrics:")
    for metric in EXPECTED_METRICS:
        items = grouped.get(metric) or []
        if not items:
            lines.append("  %s count=0 first= missing last= missing min= missing max= missing" % metric)
            continue
        stamps = [item["start_ts"] for item in items]
        values = [item["value"] for item in items]
        sample = items[0]
        lines.append(
            "  %s count=%s first=%s last=%s min=%s max=%s unit=%s device_class=%s state_class=%s"
            % (
                metric,
                len(items),
                utc_label(min(stamps)),
                utc_label(max(stamps)),
                min(values),
                max(values),
                sample["unit"],
                sample["device_class"],
                sample["state_class"],
            )
        )
    recent = sorted({row["start_ts"] for row in fetched}, reverse=True)[:5]
    lines.append("recent_hours:")
    for ts in recent:
        kind = "historical" if any(key[0] == ts for key in historical_keys) else "live_only"
        lines.append("  %s %s" % (utc_label(ts), kind))
    expected = {(row["start_ts"], row["metric"]): row for row in dataset}
    fetched_by_key = {(row["start_ts"], row["metric"]): row for row in fetched}
    lines.append("samples:")
    for key in sample_keys(dataset):
        exp = expected[key]
        found = fetched_by_key.get(key)
        if found is None:
            lines.append("  MISSING %s %s" % (key[1], utc_label(key[0])))
            continue
        state = "MATCH" if key not in mismatched else "MISMATCH"
        lines.append(
            "  %s %s %s value=%s unit=%s device_class=%s state_class=%s"
            % (
                state,
                key[1],
                utc_label(key[0]),
                found["value"],
                found["unit"],
                found["device_class"],
                found["state_class"],
            )
        )
    lines.append("POST-COMMIT VALIDATION PASSED" if ok else "POST-COMMIT VALIDATION FAILED")
    return "\n".join(lines) + "\n", ok


def verify_against_dataset(cur, dataset):
    fetched, _python_duplicates = fetch_site_rows(cur)
    duplicate_sql = sql_duplicate_count(cur)
    missing, mismatched, extras = compare_dataset(dataset, fetched)
    return fetched, duplicate_sql, missing, mismatched, extras


def commit_rows(rows, *, authorized):
    if authorized is not True:
        raise CommitError("PostgreSQL write refused")
    secrets = load_pg_secrets()
    password = secrets["postgres_password"]
    params = dataset_params(rows)
    conn = None
    committed = False
    try:
        conn = connect_postgres(secrets)
        with conn.cursor() as cur:
            affected = upsert_batches(cur, params)
            fetched, duplicate_sql, missing, mismatched, extras = verify_against_dataset(cur, rows)
        if duplicate_sql or missing or mismatched:
            conn.rollback()
            print("COMMIT FAILED", file=sys.stderr)
            print(
                "pre-commit verification failed; rolled back; postgresql_writes: 0",
                file=sys.stderr,
            )
            return 1
        conn.commit()
        committed = True
        with conn.cursor() as cur:
            fetched, duplicate_sql, missing, mismatched, extras = verify_against_dataset(cur, rows)
        report, ok = format_commit_report(
            secrets["postgres_host"],
            rows,
            fetched,
            duplicate_sql,
            affected,
            extras,
            missing,
            mismatched,
        )
        sys.stdout.write(report)
        if not ok:
            print("commit_executed: yes", file=sys.stderr)
            print("rollback_after_commit: not_possible", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        if conn is not None and not committed:
            try:
                conn.rollback()
            except Exception as roll_exc:
                print("ROLLBACK FAILED", file=sys.stderr)
                print(redact(roll_exc, password), file=sys.stderr)
                return 1
        print("COMMIT FAILED", file=sys.stderr)
        print(redact(exc, password), file=sys.stderr)
        if not committed:
            print("postgresql_writes: 0", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    mode = parse_mode(argv)
    if mode is None:
        print("unknown argument: %s" % " ".join(argv), file=sys.stderr)
        print("usage: python3 import_telemetry_history.py [--commit]", file=sys.stderr)
        return 2
    try:
        conn, query_only = open_sqlite()
        try:
            series = load_series(conn)
        finally:
            conn.close()
        built = build(series)
        sys.stdout.write(summarize(query_only, series, built))
    except ValidationFailure as exc:
        print("VALIDATION FAILED", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("postgresql_writes: 0", file=sys.stderr)
        return 1
    if mode != "commit":
        sys.stdout.write(
            "commit_support: present\n"
            "commit_executed: no\n"
            "postgresql_writes: 0\n"
            "sqlite_writes: 0\n"
        )
        return 0
    return commit_rows(built["rows"], authorized=True)


if __name__ == "__main__":
    sys.exit(main())
