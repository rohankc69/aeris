#!/usr/bin/env bash
# Start N PX4 SITL x500 quadcopters in one headless Gazebo world, plus the XRCE agent.
#
# PX4's own multi-vehicle recipe: the first instance starts the Gazebo server (HEADLESS=1)
# and spawns its model; later instances detect the running server for the same world and
# spawn into it. Instance i publishes under /px4_i (uxrce_dds_client -n px4_i) and has
# MAV_SYS_ID i+1.
set -euo pipefail

N="${PX4_VEHICLES:-3}"
WORLD="${PX4_GZ_WORLD:-aeris_forest}"
export PX4_HOME_LAT PX4_HOME_LON PX4_HOME_ALT
export HEADLESS=1
export PX4_GZ_WORLD="${WORLD}"
export PX4_GZ_MODEL=x500
export PX4_SYS_AUTOSTART=4001
# PX4's Gazebo environment: model/world paths, PX4's gz plugins and, crucially, PX4's
# server.config which loads the sensor systems. A server started without it spawns models
# whose IMU never produces timestamps.
export GZ_SIM_RESOURCE_PATH="${GZ_SIM_RESOURCE_PATH:-}" GZ_SIM_SYSTEM_PLUGIN_PATH="${GZ_SIM_SYSTEM_PLUGIN_PATH:-}"
# shellcheck disable=SC1091
. /opt/px4/build/px4_sitl_default/rootfs/gz_env.sh

# SITL parameters (PX4's rcS applies any PX4_PARAM_<name> from the environment).
export PX4_PARAM_NAV_DLL_ACT=0          # no ground-station link in headless SITL: disable datalink-loss failsafe
export PX4_PARAM_COM_RCL_EXCEPT=4       # RC loss is not a failure while in Offboard (bit 2)
export PX4_PARAM_CBRK_SUPPLY_CHK=894281 # simulated vehicles have no power module
export PX4_PARAM_COM_ARM_MIS_REQ=0      # AERIS streams setpoints; no uploaded mission required to arm
export PX4_PARAM_SIM_BAT_DRAIN="${PX4_SIM_BAT_DRAIN:-1500}"  # seconds to drain the simulated battery (default ~10 min is too short for a sweep)

echo "[sim] starting Micro XRCE-DDS agent on udp/8888"
MicroXRCEAgent udp4 -p 8888 >/tmp/xrce.log 2>&1 &

# Start the headless Gazebo server ourselves and wait until the world is being served, so every
# PX4 instance (including the first) spawns into a running world. Letting instance 1 start the
# server races its own model spawn against sensor start-up and leaves it without IMU data.
echo "[sim] starting headless Gazebo world ${WORLD}"
gz sim -s -r --verbose=1 "${PX4_GZ_WORLDS}/${WORLD}.sdf" >/tmp/gz.log 2>&1 &
for _ in $(seq 1 30); do
  if gz service -i --service "/world/${WORLD}/scene/info" >/dev/null 2>&1; then
    echo "[sim] Gazebo world ${WORLD} is up"
    break
  fi
  sleep 2
done
sleep "${PX4_WORLD_SETTLE_S:-15}"   # let the sensor systems settle before the first model spawns

cd /opt/px4

start_instance() {  # start_instance <i> [attach]
  local i="$1" attach="${2:-}"
  : >"/tmp/px4_${i}.log"
  if [ -n "$attach" ]; then
    PX4_GZ_MODEL_NAME="x500_${i}" ./build/px4_sitl_default/bin/px4 -i "$i" -d >>"/tmp/px4_${i}.log" 2>&1 &
  else
    PX4_GZ_MODEL_POSE="$((i * 4)),0,0.2,0,0,0" ./build/px4_sitl_default/bin/px4 -i "$i" -d >>"/tmp/px4_${i}.log" 2>&1 &
  fi
  # The airframe defaults re-apply NAV_DLL_ACT after the env overrides, so set it post-boot.
  for _ in $(seq 1 30); do
    if ./build/px4_sitl_default/bin/px4-param --instance "$i" set NAV_DLL_ACT 0 >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
}

sensors_faulted() {  # the first model spawned into a fresh world sometimes never gets IMU timestamps
  grep -qE "timestamp error|ekf2 missing data" "/tmp/px4_$1.log"
}

for i in $(seq 1 "$N"); do
  echo "[sim] starting PX4 instance ${i}"
  start_instance "$i"
  sleep "${PX4_SPAWN_GAP_S:-20}"
  for attempt in 1 2; do
    if sensors_faulted "$i"; then
      echo "[sim] instance ${i}: IMU fault after spawn; restarting attached to model x500_${i} (attempt ${attempt})"
      pkill -xf "\./build/px4_sitl_default/bin/px4 -i ${i} -d" || true
      sleep 3
      start_instance "$i" attach
      sleep "${PX4_SPAWN_GAP_S:-20}"
    else
      break
    fi
  done
  echo "[sim] instance ${i}: $(grep -cE 'Ready for takeoff' "/tmp/px4_${i}.log") ready, $(grep -ciE 'timestamp error|ekf2 missing' "/tmp/px4_${i}.log") sensor faults"
done

echo "[sim] up: ${N} vehicles. Logs in /tmp/px4_*.log and /tmp/xrce.log"
# Keep the container alive while the simulator runs; a faulted instance must not take it down.
wait
