"""Config flow for Jackery Energy."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback

from .api import JackeryAPI, JackeryAuthenticationError, JackeryError
from .const import (
    CONF_CIRCUIT_INTERVAL, CONF_SCAN_INTERVAL, DEFAULT_CIRCUIT_INTERVAL, DEFAULT_SCAN_INTERVAL,
    DOMAIN, MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


class JackeryEnergyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, username: str, password: str) -> dict[str, str]:
        api = JackeryAPI(username, password)
        try:
            await self.hass.async_add_executor_job(api.login)
            devices = await self.hass.async_add_executor_job(api.get_devices)
        except JackeryAuthenticationError:
            return {"base": "invalid_auth"}
        except JackeryError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Jackery login")
            return {"base": "unknown"}
        if not devices:
            return {"base": "no_devices"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            errors = await self._validate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(title=user_input[CONF_USERNAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            errors = await self._validate(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return JackeryEnergyOptionsFlow()


class JackeryEnergyOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                    vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
                vol.Required(CONF_CIRCUIT_INTERVAL,
                             default=opts.get(CONF_CIRCUIT_INTERVAL, DEFAULT_CIRCUIT_INTERVAL)):
                    vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
            }),
        )
