#!/usr/bin/env python3
import psycopg2
sec={}
for line in open("/config/secrets.yaml", encoding="utf-8"):
    if not line.strip() or line[0] in "# " or ":" not in line:
        continue
    k,v=line.split(":",1)
    sec[k.strip()]=v.strip().strip("'\"")
c=psycopg2.connect(host=sec.get("postgres_host"),port=5432,dbname=sec.get("postgres_db"),user=sec.get("postgres_user"),password=sec.get("postgres_password"))
cur=c.cursor()
cur.execute("DELETE FROM public.telemetry WHERE metric=%s",("telemetry_probe",))
print("deleted",cur.rowcount)
c.commit()
c.close()
