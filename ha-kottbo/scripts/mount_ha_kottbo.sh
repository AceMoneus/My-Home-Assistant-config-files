#!/bin/bash
LOGFILE="/config/scripts/mount_ha_kottbo.log"
MOUNT_POINT="/mnt/ha_kottbo"
SHARE="unas.moneus.lan:/var/nfs/shared/ha_kottbo"
MAX_RETRIES=5
RETRY_DELAY=10
DNS_RETRY_DELAY=5

echo "=== $(date '+%Y-%m-%d %H:%M:%S') START ===" >> "$LOGFILE"
mkdir -p "$MOUNT_POINT"

# Unmount if already mounted
if mountpoint -q "$MOUNT_POINT"; then
    echo "Already mounted, unmounting first..." >> "$LOGFILE"
    umount "$MOUNT_POINT" 2>/dev/null || true
fi

for i in $(seq 1 $MAX_RETRIES); do
    echo "Attempt $i/$MAX_RETRIES at $(date '+%H:%M:%S')..." >> "$LOGFILE"

    # Steg 1: DNS
    if ! getent hosts unas.moneus.lan > /dev/null 2>&1; then
        echo "DNS not ready, waiting ${DNS_RETRY_DELAY}s..." >> "$LOGFILE"
        sleep $DNS_RETRY_DELAY
        continue
    fi

    # Steg 2: NFS-port
    if ! nc -z -w 3 unas.moneus.lan 2049 2>/dev/null; then
        echo "NFS port 2049 not reachable, waiting ${RETRY_DELAY}s..." >> "$LOGFILE"
        sleep $RETRY_DELAY
        continue
    fi

    # Steg 3: Montera
    MOUNT_OUTPUT=$(mount -t nfs -o nfsvers=3,tcp,hard,rsize=524288,wsize=524288 "$SHARE" "$MOUNT_POINT" 2>&1)
    MOUNT_EXIT=$?

    if [ $MOUNT_EXIT -eq 0 ] && mountpoint -q "$MOUNT_POINT"; then
        echo "SUCCESS: Mounted on attempt $i" >> "$LOGFILE"
        exit 0
    else
        echo "Mount failed (exit $MOUNT_EXIT): $MOUNT_OUTPUT" >> "$LOGFILE"
        sleep $RETRY_DELAY
    fi
done

echo "=== FAILED after $MAX_RETRIES attempts at $(date '+%H:%M:%S') ===" >> "$LOGFILE"
exit 1