#!/usr/bin/env bash
cd /workspace/AiMaxBossman
echo "=== APT UPDATE ==="
apt-get update -qq > /tmp/aptup.log 2>&1; echo UPDATE_EXIT=$?
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql redis-server > /tmp/apt.log 2>&1; echo APT_EXIT=$?
tail -3 /tmp/apt.log
echo "=== START SERVICES ==="
(service postgresql start || pg_ctlcluster 16 main start) 2>&1 | tail -1; sleep 2
(service redis-server start || redis-server --daemonize yes) 2>&1 | tail -1; sleep 1
pg_isready -h 127.0.0.1 -p 5432
redis-cli ping 2>/dev/null
echo "=== PG ROLE/DB ==="
PW=$(openssl rand -hex 12)
su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='bossman'\"" | grep -q 1 \
  && su postgres -c "psql -q -c \"ALTER ROLE bossman WITH LOGIN PASSWORD '$PW';\"" \
  || su postgres -c "psql -q -c \"CREATE ROLE bossman LOGIN PASSWORD '$PW';\""
su postgres -c "createdb -O bossman bossman" 2>/dev/null || echo db_exists
sed -i '/BOSSMAN_DATABASE_URL/d; /REDIS_URL/d' /etc/profile.d/bossman_env.sh /workspace/AiMaxBossman/.env
cat >> /etc/profile.d/bossman_env.sh <<EOF
export BOSSMAN_DATABASE_URL=postgresql://bossman:$PW@127.0.0.1:5432/bossman
export REDIS_URL=redis://127.0.0.1:6379/0
EOF
cat >> /workspace/AiMaxBossman/.env <<EOF
BOSSMAN_DATABASE_URL=postgresql://bossman:$PW@127.0.0.1:5432/bossman
REDIS_URL=redis://127.0.0.1:6379/0
EOF
chmod 600 /etc/profile.d/bossman_env.sh /workspace/AiMaxBossman/.env
PGPASSWORD=$PW psql -h 127.0.0.1 -U bossman -d bossman -tAc 'select 1;' && echo PG_AUTH_OK
echo "=== FINAL PREFLIGHT ==="
python3 tools/runpod_preflight.py 2>&1 | tail -20