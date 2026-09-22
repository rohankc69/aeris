#!/usr/bin/env bash
# Start a headless Gazebo server, N PX4 SITL instances (x500 quadcopters) and the XRCE agent.
# PX4 instance i publishes under /px4_i (uxrce_dds_client -n px4_i) and has MAV_SYS_ID i+1.
set -euo pipefail

N="${PX4_VEHICLES:-3}"
WORLD="${PX4_GZ_WORLD:-aeris_forest}"
export PX4_HOME_LAT PX4_HOME_LON PX4_HOME_ALT
export GZ_SIM_RESOURCE_PATH="/opt/px4/Tools/simulation/gz/models:/opt/worlds:${GZ_SIM_RESOURCE_PATH:-}"

echo "[sim] starting headless Gazebo world ${WORLD}"
gz sim -s -r "/opt/worlds/${WORLD}.sdf" >/tmp/gz.log 2>&1 &
sleep 5

echo "[sim] starting Micro XRCE-DDS agent on udp/8888"
MicroXRCEAgent udp4 -p 8888 >/tmp/xrce.log 2>&1 &

cd /opt/px4
for i in $(seq 1 "$N"); do
  echo "[sim] starting PX4 instance ${i}"
  PX4_GZ_STANDALONE=1 \
  PX4_SYS_AUTOSTART=4001 \
  PX4_GZ_MODEL=x500 \
  PX4_GZ_MODEL_POSE="$((i * 4)),0,0.2,0,0,0" \
  PX4_GZ_WORLD="${WORLD}" \
  ./build/px4_sitl_default/bin/px4 -i "$i" -d >"/tmp/px4_${i}.log" 2>&1 &
  sleep 3
done

echo "[sim] up: ${N} vehicles. Logs in /tmp/*.log"
wait -n
