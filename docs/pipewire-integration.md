# PipeWire / Linux Integration

EQForge offers two native real-time deployment styles. Both run the *same*
`libeqforge` chain and read the *same* `~/.config/eqforge/active-dsp.json`.
Pick one; they can coexist but only one should be in your audio path.

## A. SPA filter node (`libeqforge-filter.so`)

A compiled PipeWire plugin implementing the SPA node factory
`eqforge.filter` (1 input port, 1 output port, F32 planar or interleaved,
1–8 channels, any rate). It is hosted by PipeWire itself via
`libpipewire-module-adapter`, so it appears as a regular graph node with:

* correct latency reporting (limiter look-ahead),
* hot-reload of `active-dsp.json` (mtime poll ≈1 Hz, click-guarded),
* zero Python at runtime.

### Install

```sh
make -C pipewire
sudo make -C pipewire install     # → /usr/lib/<arch>-linux-gnu/spa-0.2/eqforge/
eqforge system setup              # writes ~/.config/pipewire/pipewire.conf.d/99-eqforge.conf
systemctl --user restart pipewire pipewire-pulse wireplumber
```

The generated fragment (see `packaging/pipewire/99-eqforge.conf`):

```hocon
context.modules = [
    {   name = libpipewire-module-adapter
        args = {
            factory.name     = eqforge.filter
            node.name        = "eqforge_filter"
            node.description = "EQForge Audio Processor"
            media.class      = "Audio/Sink"
            audio.channels   = 2
            audio.position   = [ FL FR ]
            config           = "~/.config/eqforge/active-dsp.json"
        }
    }
]
```

### Route audio through it

The node shows up as an `Audio/Sink`. Make it the default so applications
play into it, and connect its output to your real speakers:

```sh
wpctl status                       # find "eqforge_filter" under Audio
wpctl set-default <eqforge-id>     # apps now play into EQForge
pw-link eqforge_filter:out_0 <your-sink>:playback_FL
pw-link eqforge_filter:out_1 <your-sink>:playback_FR
```

WirePlumber users can automate both steps with a small policy rule (an
example lives in `packaging/wireplumber/50-eqforge.lua.example`).

Verify: `pw-top` should show `eqforge_filter` in the graph while audio plays.

## B. RT host (`eqforge-rt`, JACK client)

A native JACK client — under PipeWire that means `pipewire-jack`, no jackd
needed. It is the most portable option (also works on plain JACK2/jackd) and
the daemon can wire it automatically.

```sh
make -C rthost && sudo make -C rthost install
eqforge system start --manage       # start + connect outputs to default sink
eqforge system status               # latency, rate, telemetry
```

What it does:

* registers `eqforge:in_N` / `eqforge:out_N` ports,
* processes through `libeqforge` in the JACK callback (RT-safe),
* listens on `$XDG_RUNTIME_DIR/eqforge/rt.sock` for JSON commands from the
  daemon: `reload`, `bypass`, `connect`, `status`, `quit`,
* reports look-ahead latency through the JACK latency callback, so
  PipeWire compensates A/V sync properly.

Routing all system audio through it ( PipeWire):

```sh
# point the default sink's input streams at eqforge
pw-link firefox:out_FL eqforge:in_1        # per-app
# …or let the daemon do policy wiring for the default sink:
eqforge system wire
```

For *full* system-wide capture (every app), the SPA path (A) is cleaner; the
RT host shines for per-app routing, JACK workflows and non-PipeWire setups.

## Device awareness & auto-switching

The daemon polls the PipeWire graph every ~2 s (`pw-dump`) and:

1. detects the current default sink and its stable identity
   (name, ALSA card, bluez address, kind…),
2. evaluates matching rules from `config.json` + all profiles'
   `matching.devices`,
3. when **auto-switch** is enabled and a rule matches, it activates that
   profile (hot-reloading the native host) and sends a desktop notification.

Enable in the GUI (System tab) or:

```sh
eqforge daemon …            # running
curl -X POST localhost:PORT/api/auto_switch -d '{"value": true}'
# or config.json: {"auto_switch": true}
```

Hot-plug behavior: when a Bluetooth headset connects and becomes the default
sink, your `bluez_address`-matched profile activates within ~2 s. When it
disconnects, the previous sink becomes default again; rules are re-evaluated
on every graph change. If no rule matches, the active profile is left alone
(no surprising switches).

## Application-aware audio

`eqforge streams` lists running streams with their `application.process.binary`
and `media.name`. Profiles can carry `matching.apps` rules. The daemon exposes
them via RPC (`streams`, `apply_rules_now`); automatic per-app *routing* is
deployment-specific:

* SPA path: create one filter node instance per app group and use WirePlumber
  policy rules to link apps to the right node (fragment provided in
  `packaging/wireplumber/`),
* RT path: `pw-link <app>:out_FL eqforge:in_1` per app — the daemon's `wire`
  command automates the common cases.

Per-app profiles with per-app processing chains (multiple filter instances)
are on the roadmap; the identity/rule machinery is already in place.

## Session behavior

* `packaging/systemd/eqforge-daemon.service` — user unit autostarting the
  daemon (GUI server + watcher). Enable:
  `systemctl --user enable --now eqforge-daemon`
* `packaging/systemd/eqforge-rt.service` — user unit for the RT host (written
  by `eqforge system setup`).
* `packaging/eqforge.desktop` — launches `eqforge gui`.
* Sockets live in `$XDG_RUNTIME_DIR/eqforge/` (mode 0600/0700).
* Stale sockets from crashes are detected and replaced on next start.

## Latency accounting

The limiter's look-ahead (default 1.5 ms @48k = 72 frames) is the only
processing latency. It is reported:

* SPA: `SPA_PARAM_Latency` (+ `min_ns/max_ns`),
* JACK: `jack_set_latency_callback`,
* daemon telemetry: `rt.latency_frames`,
* offline renders: compensated automatically (sample-aligned output).

With a 1024-frame quantum at 48 kHz, total added latency ≈ 21 ms + look-ahead;
lower the quantum (`pw-metadata -n settings 0 default.clock.allowed-rates`,
`force-quantum`) if you need tighter monitoring.
