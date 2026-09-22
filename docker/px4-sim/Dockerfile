# PX4 SITL + Gazebo Harmonic (headless server) + Micro XRCE-DDS Agent, for the AERIS `px4`
# compose profile. Builds PX4 from source once (10-25 min, ~4 GB). Ubuntu 24.04 to match
# ROS 2 Jazzy in the bridge image.
#
# Status: authored, not yet built in the authoring session.
FROM ubuntu:24.04

ARG PX4_VERSION=v1.15.4
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl sudo lsb-release gnupg python3 python3-pip cmake build-essential \
    && rm -rf /var/lib/apt/lists/*

# PX4 source and its Ubuntu setup script (installs toolchain and Gazebo Harmonic).
WORKDIR /opt
RUN git clone --depth 1 --branch ${PX4_VERSION} --recursive https://github.com/PX4/PX4-Autopilot.git px4 \
    && cd px4 && bash ./Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools \
    && apt-get update && apt-get install -y --no-install-recommends gz-harmonic \
    && rm -rf /var/lib/apt/lists/*

# Build SITL once so container start is fast.
RUN cd /opt/px4 && DONT_RUN=1 make px4_sitl_default gz_x500

# Micro XRCE-DDS Agent (PX4 <-> ROS 2 DDS bridge).
RUN git clone --depth 1 --branch v2.4.3 https://github.com/eProsima/Micro-XRCE-DDS-Agent.git /opt/xrce \
    && cd /opt/xrce && mkdir build && cd build \
    && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j"$(nproc)" && make install && ldconfig

COPY run_sim.sh /opt/run_sim.sh
COPY worlds/ /opt/worlds/
RUN chmod +x /opt/run_sim.sh

ENV PX4_VEHICLES=3 \
    PX4_GZ_WORLD=aeris_forest \
    PX4_HOME_LAT=46.9700 \
    PX4_HOME_LON=8.0000 \
    PX4_HOME_ALT=500

EXPOSE 8888/udp
ENTRYPOINT ["/opt/run_sim.sh"]
