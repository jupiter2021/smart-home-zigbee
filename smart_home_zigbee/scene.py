"""
Scene Controller
=================

Supports two types of scenes:
  - Hardware scenes: Stored in the Zigbee gateway NVM, read-only, can only be triggered.
  - Software scenes: Defined in config.yaml as combinations of lights, fully customizable.
"""

import logging
from typing import Optional

from .gateway import Gateway
from .config import HardwareScene, LightDevice
from .protocol import build_scene_packet
from .light import LightController

log = logging.getLogger(__name__)


class SceneController:
    """
    Control hardware and software scenes.

    Hardware scenes are pre-programmed in the Zigbee gateway by the installer.
    They cannot be modified via any known protocol -- only triggered.

    Software scenes are defined in config.yaml as lists of light names.
    When triggered, they simply turn on/off the specified lights.

    Usage::

        scenes = SceneController(gw, config, light_ctrl)
        scenes.execute("回家")       # Trigger hardware scene
        scenes.execute("会客")       # Trigger software scene (turns on specified lights)
        scenes.execute("晚安", on=False)  # Software scene: turn OFF the lights

    Args:
        gateway: Connected Gateway instance
        hardware_scenes: List of HardwareScene from config
        software_scenes: Dict of scene_name -> list of light names from config
        light_controller: LightController for software scene execution
    """

    def __init__(
        self,
        gateway: Gateway,
        hardware_scenes: list[HardwareScene],
        software_scenes: dict[str, list[str]],
        light_controller: LightController,
    ):
        self.gateway = gateway
        self.hardware_scenes = {s.name: s for s in hardware_scenes}
        self.software_scenes = software_scenes
        self.light_controller = light_controller

    def execute(self, name: str, on: bool = True) -> bool:
        """
        Execute a scene by name.

        Checks hardware scenes first, then software scenes.

        Args:
            name: Scene name (e.g., "回家", "会客")
            on: For software scenes, True=turn on, False=turn off.
                Hardware scenes ignore this parameter.

        Returns:
            True if scene was found and executed
        """
        # Try hardware scene first
        if name in self.hardware_scenes:
            return self._execute_hardware(name)

        # Try software scene
        if name in self.software_scenes:
            return self._execute_software(name, on)

        log.warning("Unknown scene: %s", name)
        log.info("Available scenes: %s", ", ".join(self.list_scenes()))
        return False

    def list_scenes(self) -> list[str]:
        """Return names of all available scenes (hardware + software)."""
        hw = [f"{name} [HW]" for name in self.hardware_scenes]
        sw = [f"{name} [SW]" for name in self.software_scenes]
        return hw + sw

    def _execute_hardware(self, name: str) -> bool:
        """Trigger a hardware scene on the gateway."""
        scene = self.hardware_scenes[name]
        packet = build_scene_packet(scene.addr, scene.ch)
        ok = self.gateway.send(packet)
        status = "ok" if ok else "FAILED"
        log.info("  %s Scene [HW]: %s (0x%02X:0x%02X)",
                 status, name, scene.addr, scene.ch)
        return ok

    def _execute_software(self, name: str, on: bool) -> bool:
        """Execute a software scene by controlling individual lights."""
        light_names = self.software_scenes[name]
        action = "ON" if on else "OFF"
        log.info("Scene [SW]: %s -> %s (%d lights)", name, action, len(light_names))

        # "*" means all lights
        if light_names == ["*"]:
            results = self.light_controller.on() if on else self.light_controller.off()
        else:
            results = []
            for light_name in light_names:
                r = self.light_controller.on(light_name) if on else self.light_controller.off(light_name)
                results.extend(r)

        return all(ok for _, ok in results) if results else False
