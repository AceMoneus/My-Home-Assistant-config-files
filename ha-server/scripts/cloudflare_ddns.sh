#!/bin/bash

API_TOKEN=$(grep 'cloudflare_api_token:' $SECRETS_FILE | cut -d'"' -f2)
ZONE_ID=$(grep 'cloudflare_zone_id:' $SECRETS_FILE | cut -d'"' -f2)
RECORD_ID=$(grep 'cloudflare_record_id:' $SECRETS_FILE | cut -d'"' -f2)
RECORD_NAME=$(grep 'cloudflare_record_name:' $SECRETS_FILE | cut -d'"' -f2)

IP=$(curl -s https://api.ipify.org)

curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
     -H "Authorization: Bearer $API_TOKEN" \
     -H "Content-Type: application/json" \
     --data "{\"type\":\"A\",\"name\":\"$RECORD_NAME\",\"content\":\"$IP\",\"ttl\":120,\"proxied\":false}"