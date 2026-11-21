"""Synchronous client for the Jackery cloud API (iot.jackeryapp.com).

Login/encryption is taken from theak/jackery-homeassistant (MIT). Endpoint knowledge
for shared devices and battery packs comes from jlopez/socketry's protocol notes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from typing import Any

import requests
from Cryptodome.Cipher import AES, PKCS1_v1_5
from Cryptodome.PublicKey import RSA
from Cryptodome.Util.Padding import pad

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://iot.jackeryapp.com"
TIMEOUT = 15
TOKEN_EXPIRED = 10402

_RSA_PUBLIC_KEY = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCVmzgJy/4XolxPnkfu32YtJqYGFLYqf9/rnVgURJED+8J9J3Pccd6+"
    "9L97/+7COZE5OkejsgOkqeLNC9C3r5mhpE4zk/HStss7Q8/5DqkGD1annQ+eoICo3oi0dITZ0Qll56Dowb8lXi6WHViVDdih/"
    "oeUwVJY89uJNtTWrz7t7QIDAQAB"
)

_HEADERS = {
    "accept": "*/*",
    "app_version": "1.0.5",
    "sys_version": "17.2",
    "platform": "1",
    "accept-language": "en-US",
    "accept-encoding": "br;q=1.0, gzip;q=0.9, deflate;q=0.8",
    "user-agent": "DxPowerProject/1.0.5 (com.hb.jackery; build:2; iOS 17.2.0) Alamofire/5.8.0",
    "model": "iPad Pro (12.9-inch) (3rd generation)",
}


class JackeryError(Exception):
    """Generic API error."""


class JackeryAuthenticationError(JackeryError):
    """Login failed."""


class JackeryAPI:
    """Minimal Jackery cloud client."""

    def __init__(self, account: str, password: str, android_id: str = "abcd1234567890ef") -> None:
        self.account = account
        self.password = password
        self.android_id = android_id
        self._token: str | None = None
        self._session = requests.Session()
        # Needed for the MQTT broker (Smart Transfer Switch control / circuits)
        self.user_id: str | None = None
        self.mqtt_password: str | None = None

    # ---------------------------------------------------------------- auth
    @property
    def mac_id(self) -> str:
        return self._mac_id()

    def _mac_id(self) -> str:
        digest = hashlib.md5(self.android_id.encode("utf-8")).digest()
        return "2" + str(uuid.UUID(bytes=digest, version=3)).replace("-", "")

    @staticmethod
    def _aes(plain: str, key: bytes) -> str:
        cipher = AES.new(key, AES.MODE_ECB)
        return base64.b64encode(cipher.encrypt(pad(plain.encode("utf-8"), AES.block_size))).decode()

    @staticmethod
    def _rsa(data: bytes) -> str:
        pem = f"-----BEGIN PUBLIC KEY-----\n{_RSA_PUBLIC_KEY}\n-----END PUBLIC KEY-----"
        cipher = PKCS1_v1_5.new(RSA.importKey(pem))
        return base64.b64encode(cipher.encrypt(data)).decode()

    def login(self) -> bool:
        login_bean = {
            "account": self.account,
            "loginType": 2,
            "macId": self._mac_id(),
            "password": self.password,
            "phone": "",
            "registerAppId": "com.hbxn.jackery",
            "verificationCode": "",
        }
        aes_key = b"1234567890123456"
        params = {
            "aesEncryptData": self._aes(json.dumps(login_bean, ensure_ascii=False), aes_key),
            "rsaForAesKey": self._rsa(aes_key),
        }
        try:
            resp = self._session.post(
                f"{BASE_URL}/v1/auth/login",
                params=params,
                headers={**_HEADERS, "upload-incomplete": "?0", "upload-draft-interop-version": "3"},
                files={"file": ("", b"", "")},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as err:
            raise JackeryError(f"Login request failed: {err}") from err

        if data.get("code") == 0 and data.get("token"):
            self._token = data["token"]
            info = data.get("data") or {}
            if info.get("userId") is not None:
                self.user_id = str(info["userId"])
            if info.get("mqttPassWord"):
                self.mqtt_password = info["mqttPassWord"]
            return True
        raise JackeryAuthenticationError(f"{data.get('msg', 'Login failed')} (code {data.get('code')})")

    # ------------------------------------------------------------- request
    def _request(self, method: str, path: str, *, params: dict | None = None,
                 form: dict | None = None) -> dict[str, Any]:
        if not self._token:
            self.login()

        for attempt in (1, 2):
            headers = {**_HEADERS, "token": self._token or ""}
            if form is None:
                headers["content-type"] = "application/json"
            try:
                resp = self._session.request(
                    method, f"{BASE_URL}{path}", headers=headers, params=params,
                    data=form, timeout=TIMEOUT,
                )
                if resp.status_code == 401 and attempt == 1:
                    self.login()
                    continue
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as err:
                raise JackeryError(f"{path}: {err}") from err

            if data.get("code") == TOKEN_EXPIRED and attempt == 1:
                _LOGGER.debug("Token expired, logging in again")
                self.login()
                continue
            if data.get("code") != 0:
                raise JackeryError(f"{path}: {data.get('msg', 'error')} (code {data.get('code')})")
            return data
        raise JackeryError(f"{path}: re-login did not help")

    # ------------------------------------------------------------ devices
    def get_devices(self) -> list[dict[str, Any]]:
        """Owned devices plus devices other accounts have shared with you."""
        devices: dict[str, dict[str, Any]] = {}

        owned = self._request("GET", "/v1/device/bind/list").get("data") or []
        for dev in owned:
            if dev.get("devId") is not None:
                devices[str(dev["devId"])] = {**dev, "shared": False}

        try:
            shared = self._request("GET", "/v1/device/bind/shared").get("data") or {}
            for rel in shared.get("receive") or []:
                lst = self._request(
                    "POST", "/v1/device/bind/share/list",
                    form={"bindUserId": rel.get("bindUserId"), "level": rel.get("level")},
                ).get("data") or []
                for dev in lst:
                    key = str(dev.get("devId"))
                    if dev.get("devId") is not None and key not in devices:
                        devices[key] = {**dev, "shared": True}
        except JackeryError as err:
            _LOGGER.debug("Shared device lookup failed (ignored): %s", err)

        return list(devices.values())

    def get_property(self, device_id: str) -> dict[str, Any]:
        """Returns {"device": {...meta...}, "properties": {...}}."""
        return self._request("GET", "/v1/device/property", params={"deviceId": device_id}).get("data") or {}

    def get_battery_packs(self, device_sn: str) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/device/battery/pack/list", params={"deviceSn": device_sn}).get("data")
        if isinstance(data, dict):
            data = data.get("list") or data.get("packs") or data.get("subDevices") or []
        return [p for p in (data or []) if isinstance(p, dict)]

    def get_today_stats(self, device_sn: str) -> Any:
        return self._request("GET", "/v1/device/stat/today", params={"deviceSn": device_sn}).get("data")
