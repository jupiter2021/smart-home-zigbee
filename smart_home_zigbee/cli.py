"""
Command-line interface for smart-home-zigbee.

Usage::

    smz on 客厅主灯          # Turn on a specific light
    smz off all              # Turn off all lights
    smz on 客厅              # Turn on all lights in zone "客厅"
    smz scene 回家            # Execute scene
    smz fresh-air on         # Turn on fresh air
    smz fresh-air on --speed high
    smz list                 # List all devices
    smz list scenes          # List all scenes
"""

import sys
import logging
import argparse

from .config import load_config
from .gateway import Gateway
from .light import LightController
from .scene import SceneController
from .fresh_air import FreshAirController


def setup_logging(verbose: bool = False):
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_on_off(args, gw, config):
    """Handle on/off commands."""
    lights = LightController(gw, config.lights)
    target = args.target or "all"

    if args.command == "on":
        results = lights.on(target)
    else:
        results = lights.off(target)

    if not results:
        print(f"No lights matched: {target}")
        print("Use 'smz list' to see available devices")
        return 1

    ok_count = sum(1 for _, ok in results if ok)
    total = len(results)
    action = "ON" if args.command == "on" else "OFF"
    print(f"\n{action}: {ok_count}/{total} lights OK")
    return 0 if ok_count == total else 1


def cmd_scene(args, gw, config):
    """Handle scene command."""
    lights = LightController(gw, config.lights)
    scenes = SceneController(
        gw, config.hardware_scenes, config.software_scenes, lights
    )

    if not args.name:
        print("Available scenes:")
        for s in scenes.list_scenes():
            print(f"  {s}")
        return 0

    on = not args.off
    ok = scenes.execute(args.name, on=on)
    return 0 if ok else 1


def cmd_fresh_air(args, gw, config):
    """Handle fresh-air command."""
    fa = FreshAirController(gw, config.fresh_air)

    if args.action == "on":
        ok = fa.on(speed=args.speed)
    elif args.action == "off":
        ok = fa.off()
    elif args.action == "speed":
        if not args.speed:
            print("Error: --speed is required for 'speed' action")
            return 1
        ok = fa.set_speed(args.speed)
    else:
        print(f"Unknown action: {args.action}")
        return 1

    return 0 if ok else 1


def cmd_list(args, gw, config):
    """Handle list command."""
    what = args.what or "lights"

    if what == "lights":
        lights = LightController(gw, config.lights)
        devices = lights.list_devices()
        if not devices:
            print("No lights configured. Check your config.yaml")
            return 0

        print(f"Gateway: {config.gateway.ip}:{config.gateway.port}")
        print(f"Lights: {len(devices)}")
        print()
        # Table header
        print(f"  {'Name':<14} {'DevNo':<8} {'DevCh':<8} {'Zone':<8}")
        print(f"  {'─'*14} {'─'*8} {'─'*8} {'─'*8}")
        for d in devices:
            print(f"  {d.name:<14} 0x{d.dev_no:02X}     0x{d.dev_ch:02X}     {d.zone}")
        print()
        # Zone summary
        zones = sorted(set(d.zone for d in devices if d.zone))
        print(f"Zones: {', '.join(zones)}")

    elif what == "scenes":
        lights = LightController(gw, config.lights)
        scenes = SceneController(
            gw, config.hardware_scenes, config.software_scenes, lights
        )
        print("Scenes:")
        for s in scenes.list_scenes():
            print(f"  {s}")

    else:
        print(f"Unknown list target: {what}")
        print("Available: lights, scenes")
        return 1

    return 0


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="smz",
        description="DNAKE Zigbee Smart Home Controller",
        epilog="If this project helps you, consider supporting the author: "
               "https://github.com/jupiter2021/smart-home-zigbee#打赏支持",
    )
    parser.add_argument("-c", "--config", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    sub = parser.add_subparsers(dest="command")

    # on / off
    p_on = sub.add_parser("on", help="Turn on lights")
    p_on.add_argument("target", nargs="?", help="Light name, zone name, or 'all'")
    p_off = sub.add_parser("off", help="Turn off lights")
    p_off.add_argument("target", nargs="?", help="Light name, zone name, or 'all'")

    # scene
    p_scene = sub.add_parser("scene", help="Execute a scene")
    p_scene.add_argument("name", nargs="?", help="Scene name (omit to list)")
    p_scene.add_argument("--off", action="store_true",
                         help="For software scenes: turn OFF instead of ON")

    # fresh-air
    p_fa = sub.add_parser("fresh-air", help="Control fresh air system")
    p_fa.add_argument("action", choices=["on", "off", "speed"],
                      help="Action to perform")
    p_fa.add_argument("--speed", choices=["low", "mid", "high"],
                      help="Wind speed (default: config default)")

    # list
    p_list = sub.add_parser("list", help="List devices or scenes")
    p_list.add_argument("what", nargs="?", choices=["lights", "scenes"],
                        help="What to list (default: lights)")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return 0

    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except ImportError as e:
        print(f"Error: {e}")
        return 1

    # For 'list' command, gateway connection is optional
    if args.command == "list":
        gw = Gateway(config.gateway.ip, config.gateway.port)
        return cmd_list(args, gw, config)

    # Connect to gateway
    gw = Gateway(config.gateway.ip, config.gateway.port)
    if not gw.connect():
        print(f"Error: Cannot connect to gateway at {config.gateway.ip}:{config.gateway.port}")
        return 1

    try:
        if args.command in ("on", "off"):
            return cmd_on_off(args, gw, config)
        elif args.command == "scene":
            return cmd_scene(args, gw, config)
        elif args.command == "fresh-air":
            return cmd_fresh_air(args, gw, config)
    finally:
        gw.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
