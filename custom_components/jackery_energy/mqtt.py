"""Short-lived connections to Jackery's cloud MQTT broker.

The broker only accepts the client id ``{userId}@APP`` (the same one the phone app
uses), so a connection here briefly bumps the phone app off. To keep that short we
connect, send, collect replies for a few seconds, and disconnect.

Envelope and command bodies were taken from the Jackery Android app 2.4.7
(``com.hbxn.control.device.cmd.box.BoxControlFormat``). CA certificate and password
derivation follow jlopez/socketry (MIT).
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad

_LOGGER = logging.getLogger(__name__)

MQTT_HOST = "emqx.jackeryapp.com"
MQTT_PORT = 8883

# Self-signed CA for emqx.jackeryapp.com, bundled in the Jackery app (res/raw/ca.crt).
CA_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIDtTCCAp2gAwIBAgIJAPvYSRLMmPACMA0GCSqGSIb3DQEBCwUAMHAxCzAJBgNV
BAYTAkNOMRIwEAYDVQQIDAlHdWFuZ2RvbmcxETAPBgNVBAcMCFNoZW56aGVuMRQw
EgYDVQQKDAtqYWNrZXJ5LmNvbTELMAkGA1UECwwCY2ExFzAVBgNVBAMMDmNhLmph
Y2tlcnkuY29tMCAXDTIyMTIyMzEwMTc0N1oYDzIwNzcwOTI1MTAxNzQ3WjBwMQsw
CQYDVQQGEwJDTjESMBAGA1UECAwJR3Vhbmdkb25nMREwDwYDVQQHDAhTaGVuemhl
bjEUMBIGA1UECgwLamFja2VyeS5jb20xCzAJBgNVBAsMAmNhMRcwFQYDVQQDDA5j
YS5qYWNrZXJ5LmNvbTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAOrf
QltVp+PDphQ20tfQbCh/YlqIAK8VkIcYXq7DlVsX1HGl5x+6UkEahzLRtZFaWRkH
HiHSvol8I+cvq6BHte0VsjKAzl7Mae7P/UyQXwpgNa+hliZHoEqflghzYvxjlZeP
eOGGcHxg1p2M8+PeNWkX5VkVTSYi/abDz86+D5y1gq7S8n+tYk1WhKFHvIrfX3nN
4QXfDO7vAQMd1uc6YdDqRanWjxIgOSDk9B+Mblz0TxCR+hnuDDQpAE4ONjByjArS
MC/QS8BIq/TL6nixzA8y0vOHySmuOLfuhFpNoO2mujhBGN/Dmq/pZwmsKSK91PxE
dn3YO8N8q7flHd/Qw4UCAwEAAaNQME4wHQYDVR0OBBYEFL/rQk0x4WclVgw3WLsl
YH3k0dvgMB8GA1UdIwQYMBaAFL/rQk0x4WclVgw3WLslYH3k0dvgMAwGA1UdEwQF
MAMBAf8wDQYJKoZIhvcNAQELBQADggEBALZM+xA4bUnO/7/0giZ3xUPEKzwFDp4G
5UPI/5grLYxp38t2M84tlJ94W/HKH+f1CYbJ6m28dSZfWtnRzQ3Tgq0whrsmYiK9
1Txcl3HPBiL7yAn3yE8DjHV+S2eSnN0o26/rcXCe+9bghSqqGaVDOJyk+Fm4l17e
Hzx99PvPGkpGUglun3UEp/Vp5ZUl9uDYT813HJ9jK80i1MDlzBJWmg7gzh27/Qls
UJLtYvgsxiBKAnK8YkAyu51Jm8uLz1BZ1RANf22vv0QUTW+SGdgc5Q1h610G9N1i
4BaijfWnto9ka32QKgZA0gHXsT3wiwdbEow0lp7y40aiXq4kazDT7ws=
-----END CERTIFICATE-----
"""

DEVICE_PROPERTY_CHANGE = "DevicePropertyChange"
QUERY_DEVICE_PROPERTY = "QueryDeviceProperty"
QUERY_CIRCUIT_PROPERTY = "QueryCircuitProperty"


@dataclass
class BoxCommand:
    """One Smart Transfer Switch command: (actionId, messageType, body)."""

    action_id: int
    body: dict[str, Any]
    message_type: str = DEVICE_PROPERTY_CHANGE


# ---- Smart Transfer Switch command builders (from BoxControlFormat) -------------
def q_device() -> BoxCommand:
    return BoxCommand(252, {"cmd": 9}, QUERY_DEVICE_PROPERTY)


def q_circuits() -> BoxCommand:
    return BoxCommand(7, {"cmd": 10}, QUERY_CIRCUIT_PROPERTY)


def set_circuit(idx: int, on: bool) -> BoxCommand:
    return BoxCommand(9, {"cmd": 12, "idx": idx, "sw": 1 if on else 0})


def rename_circuit(idx: int, name: str) -> BoxCommand:
    nm = base64.b64encode(name.encode("utf-8")).decode("ascii")
    return BoxCommand(11, {"cmd": 14, "idx": idx, "nm": nm})


def set_priorities(priorities: dict[int, int]) -> BoxCommand:
    """All circuits' priority in one go: 0 must have, 1 nice to have, 2 non-priority."""
    cir = [{"idx": int(i), "pr": int(p)} for i, p in sorted(priorities.items())]
    return BoxCommand(10, {"cmd": 13, "cir": cir})


def set_ups(on: bool) -> BoxCommand:
    return BoxCommand(4, {"cmd": 6, "ups": 1 if on else 0})


def set_backup_only(on: bool, minutes: int = 1440) -> BoxCommand:
    return BoxCommand(6, {"cmd": 8, "ps": 1 if on else 0, "pst": minutes if on else 0})


def set_supply_from_station(on: bool) -> BoxCommand:
    return BoxCommand(2, {"cmd": 4, "pss": 1 if on else 0})


def set_work_mode(mode: int) -> BoxCommand:
    return BoxCommand(17, {"cmd": 20, "en": int(mode)})


def set_backup_reserve(pct: int) -> BoxCommand:
    return BoxCommand(5, {"cmd": 7, "dt": int(pct)})


def set_nice_to_have_level(pct: int) -> BoxCommand:
    return BoxCommand(16, {"cmd": 19, "ddt": int(pct)})


def derive_password(username: str, mqtt_password_b64: str) -> str:
    key = base64.b64decode(mqtt_password_b64)
    enc = AES.new(key, AES.MODE_CBC, key[:16]).encrypt(pad(username.encode("utf-8"), AES.block_size))
    return base64.b64encode(enc).decode("ascii")


def decode_name(nm: Any) -> str | None:
    """Circuit names travel base64-encoded; fall back to the raw value."""
    if not isinstance(nm, str) or not nm:
        return None
    try:
        raw = base64.b64decode(nm, validate=True).decode("utf-8")
        if raw and raw.isprintable():
            return raw
    except (ValueError, UnicodeDecodeError):
        pass
    return nm


class JackeryMqtt:
    """Blocking helper; call from an executor thread."""

    def __init__(self, user_id: str, mac_id: str, mqtt_password: str) -> None:
        self.user_id = str(user_id)
        self.username = f"{self.user_id}@{mac_id}"
        self.password = derive_password(self.username, mqtt_password)
        self.lock = threading.Lock()
        self.log: list[dict[str, Any]] = []  # recent traffic, for diagnostics

    def _record(self, direction: str, topic: str, payload: Any) -> None:
        self.log.append({"t": round(time.time(), 1), "dir": direction, "topic": topic.split("/")[-1],
                         "payload": payload})
        del self.log[:-40]

    def exchange(self, device_sn: str, commands: list[BoxCommand], listen_s: float = 6.0) -> list[dict]:
        """Connect, publish commands in order, collect device messages, disconnect."""
        import paho.mqtt.client as mqtt  # imported lazily (blocking import)

        with self.lock:
            received: list[dict] = []
            connected = threading.Event()
            result: dict[str, Any] = {}

            def on_connect(client, userdata, flags, rc, *args):
                code = getattr(rc, "value", rc)
                result["rc"] = code
                if code == 0:
                    client.subscribe([(f"hb/app/{self.user_id}/{t}", 1)
                                      for t in ("device", "alert", "notice", "config")])
                connected.set()

            def on_message(client, userdata, msg):
                try:
                    data = json.loads(msg.payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return
                if msg.topic.endswith("/command"):
                    return  # our own publish echoed back
                self._record("in", msg.topic, data)
                received.append(data)

            kwargs: dict[str, Any] = {"client_id": f"{self.user_id}@APP", "protocol": mqtt.MQTTv311}
            if hasattr(mqtt, "CallbackAPIVersion"):
                kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
            client = mqtt.Client(**kwargs)
            client.username_pw_set(self.username, self.password)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.load_verify_locations(cadata=CA_CERT_PEM)
            client.tls_set_context(ctx)
            client.on_connect = on_connect
            client.on_message = on_message

            client.connect(MQTT_HOST, MQTT_PORT, keepalive=20)
            client.loop_start()
            try:
                if not connected.wait(10):
                    raise ConnectionError("MQTT connect timed out")
                if result.get("rc") != 0:
                    raise ConnectionError(f"MQTT connect refused (rc={result.get('rc')})")
                time.sleep(0.5)
                for cmd in commands:
                    ts = int(time.time() * 1000)
                    payload = {
                        "deviceSn": device_sn, "id": ts, "version": 0,
                        "messageType": cmd.message_type, "actionId": cmd.action_id,
                        "timestamp": ts, "body": cmd.body,
                    }
                    self._record("out", "command", payload)
                    client.publish(f"hb/app/{self.user_id}/command", json.dumps(payload), qos=1)
                    time.sleep(0.4)
                time.sleep(listen_s)
            finally:
                client.loop_stop()
                client.disconnect()
            return received
