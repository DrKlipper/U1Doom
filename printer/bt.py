"""Bluetooth on the U1 through BlueZ's D-Bus API; prints JSON for the launcher.

Runs on the printer with its own Python 3.11 and dbus_next 0.2.3 (the
firmware has no bluetoothctl). Usage:

    python3 bt.py status
    python3 bt.py scan [seconds]
    python3 bt.py pair|connect|disconnect|remove <address>
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from dbus_next import BusType, Variant
from dbus_next.errors import DBusError
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

BLUEZ = "org.bluez"
AGENT_PATH = "/u1doom/agent"
# The U1's BlueZ 5.77 is patched: Device1.Connect/Disconnect take an address
# type ("bredr", "public", "random"). "auto" keeps stock BlueZ behaviour: Connect
# picks the bearer itself, Disconnect drops all of them (read from bluetoothd).
ADDR_TYPE = "auto"


class Agent(ServiceInterface):
    """Accepts every pairing request: controllers and speakers pair with
    "just works" and have nothing to type in."""

    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        pass

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        return "0000"

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        pass

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        pass

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        pass

    @method()
    def RequestAuthorization(self, device: "o"):
        pass

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        pass

    @method()
    def Cancel(self):
        pass


def unblock_rfkill():
    # A fresh USB dongle can start soft-blocked; there is no rfkill tool.
    for entry in Path("/sys/class/rfkill").glob("rfkill*"):
        if (entry / "type").read_text().strip() == "bluetooth":
            (entry / "soft").write_text("0")


async def start_bluealsa():
    # Speakers need bluealsa's A2DP endpoint; without it BlueZ refuses them with
    # "br-connection-profile-unavailable". run.sh starts it, too.
    if subprocess.run(["pidof", "bluealsa"], stdout=subprocess.DEVNULL).returncode:
        # Own session: keeps running after this SSH command has ended.
        subprocess.Popen(["bluealsa", "-S", "-p", "a2dp-source"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(1)


async def interface(bus, path, name):
    intro = await bus.introspect(BLUEZ, path)
    return bus.get_proxy_object(BLUEZ, path, intro).get_interface(name)


async def objects(bus):
    manager = await interface(bus, "/", "org.freedesktop.DBus.ObjectManager")
    return await manager.call_get_managed_objects()


def value(props, key, default=None):
    return props[key].value if key in props else default


async def adapter_path(bus):
    for path, ifaces in (await objects(bus)).items():
        if "org.bluez.Adapter1" in ifaces:
            return path
    return None


async def status(bus):
    adapters, devices = [], []
    for path, ifaces in (await objects(bus)).items():
        if "org.bluez.Adapter1" in ifaces:
            a = ifaces["org.bluez.Adapter1"]
            adapters.append({"path": path, "address": value(a, "Address"), "name": value(a, "Name"),
                             "powered": value(a, "Powered"), "discovering": value(a, "Discovering")})
        if "org.bluez.Device1" in ifaces:
            d = ifaces["org.bluez.Device1"]
            devices.append({"address": value(d, "Address"), "name": value(d, "Alias", value(d, "Name")),
                            "icon": value(d, "Icon", ""), "rssi": value(d, "RSSI"),
                            "paired": value(d, "Paired"), "trusted": value(d, "Trusted"),
                            "connected": value(d, "Connected")})
    devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"] is None, d["name"] or ""))
    return {"adapters": adapters, "devices": devices}


async def power_on(bus):
    unblock_rfkill()
    path = await adapter_path(bus)
    if not path:
        raise RuntimeError("no_adapter")
    props = await interface(bus, path, "org.freedesktop.DBus.Properties")
    await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
    return path


async def scan(bus, seconds):
    path = await power_on(bus)
    adapter = await interface(bus, path, "org.bluez.Adapter1")
    await adapter.call_set_discovery_filter({"Transport": Variant("s", "auto")})
    await adapter.call_start_discovery()
    await asyncio.sleep(seconds)
    await adapter.call_stop_discovery()
    return await status(bus)


async def device(bus, address):
    path = await adapter_path(bus)
    if not path:
        raise RuntimeError("no_adapter")
    return path, f"{path}/dev_{address.upper().replace(':', '_')}"


async def pair(bus, address):
    await power_on(bus)
    await start_bluealsa()
    agent = Agent()
    bus.export(AGENT_PATH, agent)
    manager = await interface(bus, "/org/bluez", "org.bluez.AgentManager1")
    await manager.call_register_agent(AGENT_PATH, "NoInputNoOutput")
    await manager.call_request_default_agent(AGENT_PATH)
    _, path = await device(bus, address)
    dev = await interface(bus, path, "org.bluez.Device1")
    props = await interface(bus, path, "org.freedesktop.DBus.Properties")
    if not (await props.call_get("org.bluez.Device1", "Paired")).value:
        await dev.call_pair()
    # Trusted lets the controller reconnect by itself when its PS button is pressed.
    await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
    await dev.call_connect(ADDR_TYPE)
    await manager.call_unregister_agent(AGENT_PATH)
    return await status(bus)


async def run(command, args):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    if command == "status":
        return await status(bus)
    if command == "scan":
        return await scan(bus, int(args[0]) if args else 10)
    if command == "pair":
        return await pair(bus, args[0])
    adapter, path = await device(bus, args[0])
    if command == "connect":
        await start_bluealsa()
        await (await interface(bus, path, "org.bluez.Device1")).call_connect(ADDR_TYPE)
    elif command == "disconnect":
        await (await interface(bus, path, "org.bluez.Device1")).call_disconnect(ADDR_TYPE)
    elif command == "remove":
        await (await interface(bus, adapter, "org.bluez.Adapter1")).call_remove_device(path)
    else:
        raise RuntimeError("unknown_command")
    return await status(bus)


def main():
    try:
        result = asyncio.run(run(sys.argv[1], sys.argv[2:]))
    except DBusError as exc:
        result = {"error": exc.type, "detail": exc.text}
    except Exception as exc:   # one JSON answer for the launcher, whatever failed
        result = {"error": str(exc) or type(exc).__name__}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
