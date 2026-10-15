# USB-first remote access

```text
Mac -> Tailscale -> comma sshd -> Jetson sshd
                                 1. USB management (169.254.77.1)
                                 2. same hotspot/private LAN

comma FunctionFS bulk interface 0 -> Jetlink inference (unchanged protocol)
comma additional CDC-NCM interface -> SSH management
```

Only comma needs Tailscale and an Internet connection. Jetson does not need its
own VPN or Internet connection for USB SSH. This is an SSH jump-host arrangement,
not an exit node, advertised LAN route, bridged Wi-Fi, or USB role reversal.
Jetson remains the USB host. The existing C-to-C host policy stays installed.

USB management is opt-in because the actual comma kernel must support CDC-NCM
and the extra composite endpoints. Configuration
failure records `/dev/shm/hl-jetson-usb.json` and leaves the bulk-only gadget when
cleanup succeeds. No live gadget is unbound to enable the feature. Enable it only
offroad and verify SuperSpeed, inference and TURZX before driving. Unexpected
enumeration/bind failure requires disabling USB management and rebooting offroad;
passing unit tests is not physical compatibility validation.

NCM and FunctionFS share a USB controller. An inference transport reset or UDC
rebind also interrupts USB SSH; this is not a second physical cable. Do not
assume USB-only software updates survive an inference-server restart. Prove
that field case before enabling automatic rollout; the existing offroad lease
and rollback stay fail-closed, and same-hotspot SSH is the recovery option.
By default `sync`, `rollback` and `recover` use LAN even when ordinary SSH chose
USB. With no LAN they refuse the update, not the diagnostic shell. Only after
proving service restart, reconnection and recovery on the actual pair, run
`manage_jetson.py usb-updates on --confirm-field-checks` to qualify USB updates.

## One-Time Setup

First enable USB management on comma (below), disable ADB, and reboot offroad
with the Jetson USB cable attached. Network configuration runs after FunctionFS
binds the controller: AGNOS 4.9 exposes NCM interface/MAC attributes only then.
Kernel-generated MAC addresses are supported; fixed MACs are not required.

On the prepared carrot-jetson host, via its already authenticated LAN SSH account:

```sh
sudo python3 /tmp/usb_management.py install-host
```

Copy this repository's `tools/jetlink/usb_management.py` to that path first. This
adds only `hl-jetson-usb.nmconnection` and invokes the image's protected identity
save. Existing Wi-Fi profiles and keys are preserved. The installer requires one
CDC-NCM interface under USB `1209:0001`, revision `0102`. Its profile matches the
exact USB port path plus `cdc_ncm` driver, uses `169.254.77.1/30`, never supplies a
default route, and survives the image's volatile `/etc` overlay. No SSD reflash,
root-filesystem remount, upstream service replacement, or inference restart is
needed. Remove this profile and run `protected_storage.py save` to undo it.
Keep using that Jetson USB port (and hub topology). Moving the cable to a different
port requires reviewing/removing this profile and running `install-host` again
over LAN. An exact legacy fixed-MAC profile migrates automatically; customized
profiles are refused rather than overwritten.

On the intended comma checkout, with ignition off:

```sh
python3 tools/jetlink/manage_jetson.py usb on
```

This creates a comma-specific SSH key if missing and enables the new descriptors
for the next offroad reboot. The comma address is `169.254.77.2/30`. Export only
`/data/hl-jetson/id_ed25519.pub`, append it to the Jetson's existing authorized
keys using the already authenticated maintenance connection, then run the
Jetson's `/usr/lib/carrot-jetlink-storage/protected_storage.py save` through sudo.
Never copy a private key into Git, an update bundle, or the other device.

Pair using the independently verified Jetson Ed25519 host key and fingerprint:

```sh
python3 tools/jetlink/manage_jetson.py pair --host 169.254.77.1 \
  --host-key-file /data/hl-jetson/host.pub --fingerprint SHA256:<verified-fingerprint>
python3 tools/jetlink/manage_jetson.py shell
```

The existing LAN pairing procedure works too. Once paired, management tries a
direct USB route with the correct source address, owned NCM function/MAC and carrier before trying
fresh USB-reported LAN addresses and saved LAN addresses. A stale USB address is
never sent through Wi-Fi's default gateway. Host-key pinning applies to both
paths. First pairing and real SSH login are mandatory; an IP is not identity.

## Tailscale On Comma

The opt-in installer targets only ARM64 AGNOS comma hardware. It downloads the
official static build, checks a pinned SHA256, and stores binaries and private
account state under `/data/hl-comma-remote`. It refuses a competing Tailscale
installation. The matching branch's `comma_remote` manager process recreates
the runtime systemd registration at boot, without writing the read-only OS.
Switching to a branch without this process removes that boot guarantee.

```sh
sudo python3 tools/jetlink/setup_comma_remote.py install --confirm-parked
sudo python3 tools/jetlink/setup_comma_remote.py login
sudo python3 tools/jetlink/setup_comma_remote.py status
```

Open the login URL as the device owner. Account enrollment/sharing cannot be
completed while comma is absent. No auth key or account state is committed.
DNS takeover, accepted subnet routes, exit-node use, route advertising and
Tailscale SSH are disabled. Ordinary comma SSH must already be enabled with the
owner's authorized key; sharing the Tailscale node does not install SSH keys or
grant root access. Restrict tailnet access to the intended operator and ports.

After normal SSH to the comma's Tailscale address, run `manage_jetson.py shell`.
Alternatively use OpenSSH ProxyJump from Mac with separate pinned host identities
and the Mac's already enrolled keys. No agent forwarding is required. SSH local
port forwarding can expose a selected Jetson service to the Mac without public
ports or subnet routing.

To stop remote access while retaining its identity:

```sh
sudo python3 tools/jetlink/setup_comma_remote.py disable
```

## Required Field Checks

1. Reboot Jetson and confirm the static USB profile and old authorized keys survive.
2. Connect the actual comma and check its management status, direct route and pinned SSH.
3. Unplug Ethernet and test USB SSH with Jetson Wi-Fi disconnected. Reconnect USB
   and verify recovery, SuperSpeed, real inference, and actual TURZX frames.
4. Re-enable the shared hotspot and prove LAN fallback after USB management loss.
5. Enroll comma in Tailscale, test from a different Internet connection, and repeat
   after reboot. Verify the operator's actual SSH access, not just Tailscale ping.
6. Run managed updates only parked with fresh offroad state. Automatic rollout
   remains disabled until the separate maintenance checklist passes.

On 2026-10-15 the actual C4/AGNOS 18.4 and carrot-jetson R2 pair passed USB SSH,
reboot persistence, the existing comma Tailscale SSH hop, managed install,
rollback/reinstall, unchanged-content skipping, and 1,200 synthetic inference
frames at 20 Hz (32.9 ms mean, 36.8 ms p95, one frame over 50 ms). Fresh HUD
snapshots arrived over the same USB link. Native Jetson GPU renders of dark/light
1920x480 fixtures matched local/transported state pixel-for-pixel.

TURZX was absent: real panel output, sustained display/inference concurrency,
vehicle camera inputs, live phone media, driving and a different-Internet VPN
path remain unverified. Automatic rollout and USB-only updates remain disabled.

Official static installation documentation:
<https://tailscale.com/docs/install/linux#static-binaries>.
