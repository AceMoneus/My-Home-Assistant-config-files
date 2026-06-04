#!/bin/bash

LOGFILE="/config/scripts/mount_ha_kottbo.log"
MOUNT_POINT="/mnt/ha_kottbo"
SHARE="unas.moneus.lan:/var/nfs/shared/ha_kottbo"
MAX_RETRIES=5
RETRY_DELAY=10

echo "=== $(date '+%Y-%m-%d %H:%M:%S') START ===" >> "$LOGFILE"

mkdir -p "$MOUNT_POINT"
umount "$MOUNT_POINT" 2>/dev/null || true

for i in $(seq 1 $MAX_RETRIES); do
    echo "Attempt $i/$MAX_RETRIES..." >> "$LOGFILE"

    if getent hosts unas.moneus.lan > /dev/null 2>&1; then
        if mount -t nfs -o nfsvers=3,tcp,hard,intr,rsize=524288,wsize=524288 "$SHARE" "$MOUNT_POINT" >> "$LOGFILE" 2>&1; then
            if mountpoint -q "$MOUNT_POINT"; then
                echo "SUCCESS: Mounted on attempt $i" >> "$LOGFILE"
                exit 0
            fi
        fi
    else
        echo "DNS not ready yet..." >> "$LOGFILE"
    fi

    sleep $RETRY_DELAY
done

echo "FAILED after $MAX_RETRIES attempts" >> "$LOGFILE"
exit 1
