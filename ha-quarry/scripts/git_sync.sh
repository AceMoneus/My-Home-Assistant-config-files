#!/bin/bash

REPO_DIR="/config/git_backup"
CONFIG_DIR="/config"
SECRETS_FILE="/config/secrets.yaml"

GITHUB_TOKEN=$(grep 'github_token:' $SECRETS_FILE | cut -d'"' -f2)
GITHUB_USER=$(grep 'github_username:' $SECRETS_FILE | cut -d'"' -f2)
GITHUB_REPO=$(grep 'github_repo:' $SECRETS_FILE | cut -d'"' -f2)
SERVER_NAME=$(grep 'github_server_name:' $SECRETS_FILE | cut -d'"' -f2)

cd $REPO_DIR || exit 1

git config pull.rebase false

git pull origin main || {
    echo "Pull failed, trying to resolve..."
    git reset --hard origin/main
    git pull origin main
}

mkdir -p $SERVER_NAME/.includes
mkdir -p $SERVER_NAME/esphome
mkdir -p $SERVER_NAME/scripts

cp $CONFIG_DIR/configuration.yaml $REPO_DIR/$SERVER_NAME/ 2>/dev/null
cp $CONFIG_DIR/automations.yaml $REPO_DIR/$SERVER_NAME/ 2>/dev/null

if [ -d "$CONFIG_DIR/.includes" ]; then
    cp -r $CONFIG_DIR/.includes/* $REPO_DIR/$SERVER_NAME/.includes/ 2>/dev/null
fi

if [ -d "$CONFIG_DIR/esphome" ]; then
    cp -r $CONFIG_DIR/esphome/* $REPO_DIR/$SERVER_NAME/esphome/ 2>/dev/null
fi

if [ -d "$CONFIG_DIR/scripts" ]; then
    cp -r $CONFIG_DIR/scripts/* $REPO_DIR/$SERVER_NAME/scripts/ 2>/dev/null
fi

git add .
if git diff --staged --quiet; then
    echo "No changes to commit"
else
    git commit -m "Commit bot $SERVER_NAME - $(date '+%Y-%m-%d %H:%M:%S')"
    git push https://${GITHUB_USER}:${GITHUB_TOKEN}@github.com/${GITHUB_USER}/${GITHUB_REPO}.git main && echo "Sync completed!" || echo "Push failed!"
fi
