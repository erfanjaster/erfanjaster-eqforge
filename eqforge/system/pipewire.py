"""PipeWire / WirePlumber integration.

Discovery is done through the PipeWire CLI tools (`pw-dump`, `pw-metadata`,
`pw-link`) rather than linking libpipewire into Python: this keeps the
control plane dependency-free, survives daemon restarts, and works with any
PipeWire >= 0.3.30. All functions degrade gracefully when PipeWire is absent
(returning empty results / raising SystemError only when explicitly asked).

Device model (eqforge.system.model.Device):
    id, name, description, direction (sink/source), media_class,
    profile properties (alsa card name, bluetooth codec...), stable `identity`
    keys used for profile matching.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from eqforge.errors import SystemError
from eqforge.log import get_logger

log = get_logger("system.pipewire")


def pipewire_available() -> bool:
    return shutil.which("pw-dump") is not None


def _run(cmd: list[str], timeout: float = 15.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise SystemError(f"{cmd[0]} failed: {e}",
                          hint="is PipeWire installed and the session running?")
    if r.returncode != 0:
        raise SystemError(f"{cmd[0]} exited {r.returncode}: "
                          f"{r.stderr.strip()[:200]}")
    return r.stdout


@dataclass
class Device:
    id: int
    name: str
    description: str
    direction: str                      # "sink" | "source"
    media_class: str
    node_name: str = ""
    props: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """Best-effort human category used by matching + UI."""
        p = {k.lower(): v for k, v in self.props.items()}
        api = str(p.get("api.alsa.path", "")) + str(p.get("api.bluez5.address", ""))
        if "bluez" in json.dumps(p).lower():
            return "bluetooth"
        if "usb" in str(p.get("device.bus", "")).lower():
            return "usb"
        if "hdmi" in self.description.lower() or "hdmi" in self.name.lower():
            return "hdmi"
        if p.get("device.form-factor") in ("headphone", "headset"):
            return "headphones"
        if "speaker" in self.description.lower() or \
           p.get("device.form-factor") == "speakers":
            return "speakers"
        return "other"

    def identity(self) -> dict:
        """Stable-ish keys a matching rule can use."""
        return {
            "name": self.name,
            "node_name": self.node_name,
            "description": self.description,
            "kind": self.kind,
            "alsa_card": self.props.get("api.alsa.card.name", ""),
            "bluez_address": self.props.get("api.bluez5.address", ""),
            "bluez_name": self.props.get("api.bluez5.device", ""),
            "serial": self.props.get("device.serial", ""),
            "vendor": self.props.get("device.vendor.name", ""),
        }

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "description": self.description, "direction": self.direction,
                "media_class": self.media_class, "node_name": self.node_name,
                "kind": self.kind, "identity": self.identity()}


@dataclass
class Stream:
    id: int
    name: str                  # application name
    node_name: str             # e.g. "alsa_output.pci-...-analog-stereo"
    binary: str                # e.g. "firefox"
    media_class: str
    direction: str             # "playback" (app->sink) | "capture"

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "node_name": self.node_name,
                "binary": self.binary, "media_class": self.media_class,
                "direction": self.direction}


def list_devices(direction: str | None = None) -> list[Device]:
    """Audio sink/source nodes currently in the graph."""
    if not pipewire_available():
        return []
    dump = json.loads(_run(["pw-dump"]))
    devices: list[Device] = []
    for obj in dump:
        info = obj.get("info", {})
        props = info.get("props", {})
        mc = str(props.get("media.class", ""))
        if mc not in ("Audio/Sink", "Audio/Source", "Audio/Duplex",
                      "Stream/Output/Audio", "Stream/Input/Audio"):
            continue
        if mc.startswith("Stream/"):
            continue  # handled by list_streams
        direction_ = "sink" if "Sink" in mc or mc == "Audio/Duplex" else "source"
        if direction and direction_ != direction:
            continue
        devices.append(Device(
            id=int(obj.get("id", 0)),
            name=str(props.get("node.name", "")),
            description=str(props.get("node.description",
                                      props.get("node.name", ""))),
            direction=direction_,
            media_class=mc,
            node_name=str(props.get("node.name", "")),
            props=props,
        ))
    return devices


def list_streams() -> list[Stream]:
    """Application audio streams currently in the graph."""
    if not pipewire_available():
        return []
    dump = json.loads(_run(["pw-dump"]))
    streams: list[Stream] = []
    for obj in dump:
        info = obj.get("info", {})
        props = info.get("props", {})
        mc = str(props.get("media.class", ""))
        if mc == "Stream/Output/Audio":
            direction = "playback"
        elif mc == "Stream/Input/Audio":
            direction = "capture"
        else:
            continue
        streams.append(Stream(
            id=int(obj.get("id", 0)),
            name=str(props.get("media.name",
                               props.get("node.name", "?"))),
            node_name=str(props.get("node.name", "")),
            binary=str(props.get("application.process.binary", "")),
            media_class=mc,
            direction=direction,
        ))
    return streams


def default_sink() -> str | None:
    """Current default audio sink node name (via pw-metadata)."""
    if not pipewire_available() or not shutil.which("pw-metadata"):
        return None
    try:
        out = _run(["pw-metadata", "-n", "settings", "0"])
    except SystemError:
        return None
    for line in out.splitlines():
        if "default.audio.sink" in line:
            # format: ... "default.audio.sink" ... : "...": name
            try:
                parts = line.split('"')
                # find the value after the key
                if "default.audio.sink" in parts:
                    idx = parts.index("default.audio.sink")
                    for cand in parts[idx + 1:]:
                        if cand.strip() and cand not in (" ", ":", "s"):
                            return cand
            except ValueError:
                pass
    return None


def set_default_sink(node_name: str) -> None:
    if not shutil.which("pw-metadata"):
        raise SystemError("pw-metadata not found")
    _run(["pw-metadata", "-n", "settings", "0", "default.audio.sink",
          node_name])
    log.info("default sink -> %s", node_name)


def graph_snapshot() -> dict[str, Any]:
    """Cheap hashable snapshot used for hotplug detection."""
    return {
        "devices": [d.to_dict() for d in list_devices()],
        "streams": [s.to_dict() for s in list_streams()],
        "default_sink": default_sink(),
    }


def link_info() -> dict:
    """Which ports are connected to what (pw-link -i -o)."""
    if not shutil.which("pw-link"):
        return {}
    out = _run(["pw-link", "-i", "-o"])
    graph: dict[str, list[str]] = {}
    current = None
    for line in out.splitlines():
        if not line.startswith(" ") and line.strip().endswith(":"):
            current = line.strip()[:-1]
            graph[current] = []
        elif current and line.strip():
            graph[current].append(line.strip())
    return graph
