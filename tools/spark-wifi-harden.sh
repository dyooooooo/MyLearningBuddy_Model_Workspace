#!/usr/bin/env bash
# Disable WiFi power save on DGX Spark and persist via NetworkManager.
# Requires sudo. Run: sudo tools/spark-wifi-harden.sh
set -euo pipefail

WIFI_IF="${WIFI_IF:-wlP9s9}"
CONN_NAME="${CONN_NAME:-HUAWEI-2.4G-Eason}"

echo "=== Spark WiFi hardening ==="
echo "Interface: $WIFI_IF"
echo "NM profile: $CONN_NAME"
echo ""

if ! nmcli -t -f NAME connection show | grep -qxF "$CONN_NAME"; then
  echo "ERROR: NM connection '$CONN_NAME' not found." >&2
  echo "List profiles: nmcli connection show" >&2
  exit 1
fi

if ! iw dev "$WIFI_IF" info >/dev/null 2>&1; then
  echo "ERROR: WiFi interface '$WIFI_IF' not found." >&2
  exit 1
fi

# Immediate: driver-level power save off
iw dev "$WIFI_IF" set power_save off
echo "OK: iw power_save off"

# Persistent: NM powersave 2 = disable (0=default, 1=ignore, 2=disable)
nmcli connection modify "$CONN_NAME" 802-11-wireless.powersave 2
echo "OK: NM 802-11-wireless.powersave=2"

# Prefer this profile when multiple WiFi profiles exist
nmcli connection modify "$CONN_NAME" connection.autoconnect-priority 100
echo "OK: connection.autoconnect-priority=100"

# Re-apply active connection so NM picks up profile without full disconnect
if nmcli -t -f DEVICE,STATE device status | grep -q "^${WIFI_IF}:connected"; then
  nmcli device reapply "$WIFI_IF" >/dev/null 2>&1 || true
  echo "OK: device reapply $WIFI_IF"
fi

echo ""
echo "--- Verify ---"
iw dev "$WIFI_IF" get power_save
nmcli -g 802-11-wireless.powersave,connection.autoconnect-priority connection show "$CONN_NAME"

echo ""
echo "Done. For best stability, also plug Ethernet (enP7s7) and reserve 192.168.1.10 on the router."
