from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription(
        [
            DeclareLaunchArgument("ws_port", default_value="8765"),
            DeclareLaunchArgument("vehicles", default_value="['drone-01:1','drone-02:2','drone-03:3']"),
            Node(
                package="aeris_px4_bridge",
                executable="bridge_node",
                name="aeris_px4_bridge",
                output="screen",
                parameters=[
                    {
                        "ws_port": LaunchConfiguration("ws_port"),
                        "vehicles": LaunchConfiguration("vehicles"),
                    }
                ],
            ),
        ]
    )
