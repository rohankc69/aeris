from setuptools import find_packages, setup

package_name = "aeris_px4_bridge"

setup(
    name=package_name,
    version="0.0.1",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/bridge.launch.py"]),
    ],
    install_requires=["setuptools", "websockets>=12"],
    zip_safe=True,
    maintainer="AERIS contributors",
    maintainer_email="kcrohanraj@gmail.com",
    description="PX4 <-> AERIS bridge node",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "bridge_node = aeris_px4_bridge.bridge_node:main",
        ],
    },
)
