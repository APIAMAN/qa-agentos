#!/bin/sh
set -e

# Re-running would regenerate certs/token under a running engine and break worker TLS.
if [ -s /hatchet/config/token.txt ] && [ -f /hatchet/keys/certs/ca.cert ]; then
  echo 'Hatchet already set up; skipping'
  exit 0
fi

rm -rf /hatchet/config/*
mkdir -p /hatchet/keys/certs /hatchet/config

echo 'Running Hatchet quickstart bootstrap...'
/hatchet/hatchet-admin quickstart --skip seed --cert-dir /hatchet/keys/certs --generated-config-dir /hatchet/config --overwrite=true

cat << 'EOF' > /hatchet/config/database.yaml
cacheDuration: 5000000000
dbName: qa_agentos_hatchet
ddlPoolMaxConns: 5
ddlPoolMinConns: 1
enforceUtcTimezone: true
host: postgres
logger:
  format: console
  level: warn
maxConnIdleTime: 60000000000
maxConnLifetime: 900000000000
maxConns: 50
minConns: 1
password: postgres
port: 5432
readReplicaMaxConns: 50
readReplicaMinConns: 10
seed:
  adminEmail: admin@example.com
  adminName: Admin
  adminPassword: Admin123!!
  defaultTenantId: 707d0855-80ab-4e1f-a156-f1c4546cbf52
  defaultTenantName: Default
  defaultTenantSlug: default
sslMode: disable
username: postgres
EOF

sed -i '/cookie:/a\    domain: localhost' /hatchet/config/server.yaml
sed -i 's/kind: rabbitmq/kind: postgres/' /hatchet/config/server.yaml
sed -i 's/grpcBindAddress: 127.0.0.1/grpcBindAddress: 0.0.0.0/' /hatchet/config/server.yaml
sed -i 's/grpcBroadcastAddress: 127.0.0.1:7070/grpcBroadcastAddress: hatchet:7077/' /hatchet/config/server.yaml
sed -i 's/grpcPort: 7070/grpcPort: 7077/' /hatchet/config/server.yaml

echo 'Seeding tenant...'
/hatchet/hatchet-admin seed --config /hatchet/config || true

echo 'Generating API token...'
/hatchet/hatchet-admin token create --config /hatchet/config --tenant-id 707d0855-80ab-4e1f-a156-f1c4546cbf52 --name default > /hatchet/config/token.txt

echo 'Hatchet setup completed successfully'
exit 0
