# ROS 2 Jazzy + px4_msgs + aeris_px4_bridge.
# Status: authored, not yet built in the authoring session.
FROM ros:jazzy-ros-base

ARG PX4_MSGS_VERSION=release/1.16
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git python3-pip python3-websockets python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ros_ws
# px4_msgs is built in its own layer so editing the bridge package never rebuilds it.
RUN git clone --depth 1 --branch ${PX4_MSGS_VERSION} https://github.com/PX4/px4_msgs.git src/px4_msgs \
    && . /opt/ros/jazzy/setup.sh && colcon build --symlink-install --packages-select px4_msgs

COPY ros_ws/src/aeris_px4_bridge src/aeris_px4_bridge
RUN . /opt/ros/jazzy/setup.sh && colcon build --symlink-install --packages-select aeris_px4_bridge

COPY docker/bridge/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8765
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "aeris_px4_bridge", "bridge.launch.py"]
