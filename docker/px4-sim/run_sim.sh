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
export GZ_SIM_RESOURCE_PATH="/opt/px4/Tools/simulation/gz/models:/opt/px4/Tools/simulation/gz/worlds:${GZ_SIM_RESOURCE_PATH:-}"

# SITL parameters (PX4's rcS applies any PX4_PARAM_<name> from the environment).
export PX4_PARAM_NAV_DLL_ACT=0          # no ground-station link in headless SITL: disable datalink-loss failsafe
export PX4_PARAM_COM_RCL_EXCEPT=4       # RC loss is not a failure while in Offboard (bit 2)
export PX4_PARAM_CBRK_SUPPLY_CHK=894281 # simulated vehicles have no power module
export PX4_PARAM_COM_ARM_MIS_REQ=0      # AERIS streams setpoints; no uploaded mission required to arm

echo "[sim] starting Micro XRCE-DDS agent on udp/8888"
MicroXRCEAgent udp4 -p 8888 >/tmp/xrce.log 2>&1 &

cd /opt/px4
for i in $(seq 1 "$N"); do
  echo "[sim] starting PX4 instance ${i} (headless gz world ${WORLD})"
  PX4_GZ_MODEL_POSE="$((i * 4)),0,0.2,0,0,0" \
  ./build/px4_sitl_default/bin/px4 -i "$i" -d >"/tmp/px4_${i}.log" 2>&1 &
  # Give the first instance time to bring up the Gazebo server before others spawn into it.
  if [ "$i" -eq 1 ]; then sleep 15; else sleep 5; fi
  # The airframe defaults re-apply NAV_DLL_ACT after the env overrides, so set it post-boot.
  for _ in $(seq 1 30); do
    if ./build/px4_sitl_default/bin/px4-param --instance "$i" set NAV_DLL_ACT 0 >/dev/null 2>&1; then
      echo "[sim] instance ${i}: NAV_DLL_ACT=0 (no GCS link required to arm)"
      break
    fi
    sleep 2
  done
done

echo "[sim] up: ${N} vehicles. Logs in /tmp/px4_*.log and /tmp/xrce.log"
wait -n
