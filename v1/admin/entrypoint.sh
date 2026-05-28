#!/bin/bash
set -e

: "${THUNDER_FORGE_DIR:?THUNDER_FORGE_DIR must be set}"

if [ -f /ssh/id_ed25519 ]; then
    cp /ssh/id_ed25519 /tmp/ssh_key
    chmod 400 /tmp/ssh_key
fi

python -c "from thunder_admin.bootstrap import bootstrap; bootstrap()"

exec streamlit run thunder_admin/app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false \
    --client.showSidebarNavigation=false
