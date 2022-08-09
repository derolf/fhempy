import functools
import logging
import re
import traceback
from datetime import datetime

from ..utils import run_blocking
from .const import (
    COUNT_FORCE_UPDATE_AFTER_COMMAND,
    DATE_FORMAT,
    INITIAL_STATUS_DELAY_AFTER_COMMAND,
    INTERVAL_FORCE_UPDATE_AFTER_COMMAND,
    RECHECK_STATUS_DELAY_AFTER_COMMAND,
    REGION_CANADA,
    REGIONS,
    START_FORCE_UPDATE_AFTER_COMMAND,
    TOPIC_UPDATE,
    VEHICLE_ENGINE_TYPE,
    VEHICLE_LOCK_ACTION,
)
from .KiaUvoApiImpl import KiaUvoApiImpl
from .Token import Token

_LOGGER = logging.getLogger(__name__)


class Vehicle:
    def __init__(
        self,
        token: Token,
        kia_uvo_api: KiaUvoApiImpl,
        unit_of_measurement: str,
        enable_geolocation_entity: bool,
        region: str,
    ):
        self.token: Token = token
        self.kia_uvo_api: KiaUvoApiImpl = kia_uvo_api
        self.unit_of_measurement: str = unit_of_measurement
        self.enable_geolocation_entity: bool = enable_geolocation_entity
        self.region: str = region
        self.name = token.vehicle_name
        self.model = token.vehicle_model
        self.id = token.vehicle_id
        self.registration_date = token.vehicle_registration_date
        self.vehicle_data = {}
        self.engine_type = None
        self.last_updated: datetime = datetime.min
        self.force_update_try_caller = None
        self.topic_update = TOPIC_UPDATE.format(self.id)
        self.current_ev_battery = None
        _LOGGER.debug(f"Received token into Vehicle Object {vars(token)}")

    async def update(self):
        try:
            previous_vehicle_status = self.get_child_value("vehicleStatus")
            previous_vehicle_location = self.get_child_value("vehicleLocation")
            self.vehicle_data = await run_blocking(
                functools.partial(
                    self.kia_uvo_api.get_cached_vehicle_status, self.token
                )
            )
            self.set_last_updated()
            self.set_engine_type()
            if self.enable_geolocation_entity:
                await run_blocking(
                    functools.partial(
                        self.set_geocoded_location, previous_vehicle_location
                    )
                )

            if (
                not self.get_child_value("vehicleStatus.engine")
                and previous_vehicle_status is not None
                and not previous_vehicle_status["engine"]
                and self.get_child_value("vehicleStatus.evStatus.batteryStatus") == 0
                and previous_vehicle_status["evStatus"]["batteryStatus"] != 0
            ):
                _LOGGER.debug(
                    "zero battery api error, force_update started to correct data"
                )
                await self.force_update()

        except Exception as ex:
            _LOGGER.error(
                "Exception in update : %s - traceback: %s",
                ex,
                traceback.format_exc(),
            )

    async def force_update(self):
        await run_blocking(
            functools.partial(self.kia_uvo_api.update_vehicle_status, self.token)
        )
        await self.update()

    async def force_update_loop_start(self):
        if self.kia_uvo_api.last_action_tracked:
            # self.force_update_try_caller = async_call_later(
            #    self.hass,
            #    INITIAL_STATUS_DELAY_AFTER_COMMAND,
            #    self.check_action_completed_loop,
            # )
            pass
        else:
            self.force_update_try_count = 0
            # self.force_update_try_caller = async_call_later(
            #    self.hass, START_FORCE_UPDATE_AFTER_COMMAND, self.force_update_loop
            # )

    async def check_action_completed_loop(self, _):
        await run_blocking(
            functools.partial(self.kia_uvo_api.check_last_action_status, self.token)
        )
        if self.kia_uvo_api.last_action_completed:
            self.kia_uvo_api.last_action_xid = None
            await self.update()
        # else:
        #    async_call_later(
        #        self.hass,
        #        RECHECK_STATUS_DELAY_AFTER_COMMAND,
        #        self.check_action_completed_loop,
        #    )

    async def force_update_loop(self, _):
        _LOGGER.debug(
            f"force_update_loop start {self.force_update_try_count} {COUNT_FORCE_UPDATE_AFTER_COMMAND}"
        )
        if self.force_update_try_count == COUNT_FORCE_UPDATE_AFTER_COMMAND:
            self.force_update_try_count = 0
            return

        last_updated: datetime = self.last_updated
        _LOGGER.debug(f"force_update_loop last_updated {last_updated}")

        await self.force_update()
        _LOGGER.debug(
            f"force_update_loop force_update_finished {last_updated} {self.last_updated}"
        )
        if last_updated == self.last_updated:
            self.force_update_try_count = self.force_update_try_count + 1
            # self.force_update_try_caller = async_call_later(
            #    self.hass, INTERVAL_FORCE_UPDATE_AFTER_COMMAND, self.force_update_loop
            # )

    def set_geocoded_location(self, old_vehicle_location):
        old_lat = None
        old_lon = None
        old_geocode = None
        if (
            not old_vehicle_location is None
            and old_vehicle_location.get("coord") is not None
        ):
            old_lat = old_vehicle_location["coord"]["lat"]
            old_lon = old_vehicle_location["coord"]["lon"]
            old_geocode = old_vehicle_location.get("geocodedLocation", None)

        new_lat = self.get_child_value("vehicleLocation.coord.lat")
        new_lon = self.get_child_value("vehicleLocation.coord.lon")

        if self.vehicle_data.get("vehicleLocation") is None:
            self.vehicle_data["vehicleLocation"] = {}

        if (old_lat != new_lat or old_lon != new_lon) or old_geocode is None:
            self.vehicle_data["vehicleLocation"][
                "geocodedLocation"
            ] = self.kia_uvo_api.get_geocoded_location(new_lat, new_lon)
        else:
            self.vehicle_data["vehicleLocation"]["geocodedLocation"] = old_geocode

    async def lock_action(self, action: VEHICLE_LOCK_ACTION):
        await run_blocking(
            functools.partial(self.kia_uvo_api.lock_action, self.token, action.value)
        )
        await self.force_update_loop_start()

    async def refresh_token(self):
        _LOGGER.debug(
            f"Refresh token started {self.token.valid_until} {datetime.now()} {self.token.valid_until <= datetime.now().strftime(DATE_FORMAT)}"
        )
        if self.token.valid_until <= datetime.now().strftime(DATE_FORMAT):
            _LOGGER.debug("Refresh token expired")
            await run_blocking(functools.partial(self.login))
            return True
        return False

    async def start_climate(self, set_temp, duration, defrost, climate, heating):
        if set_temp is None:
            set_temp = 21
        if duration is None:
            duration = 5
        if defrost is None:
            defrost = False
        if climate is None:
            climate = True
        if heating is None:
            heating = False
        if (
            self.engine_type == VEHICLE_ENGINE_TYPE.EV
            and REGIONS[self.region] == REGION_CANADA
        ):
            await run_blocking(
                functools.partial(
                    self.kia_uvo_api.start_climate_ev,
                    self.token,
                    set_temp,
                    duration,
                    defrost,
                    climate,
                    heating,
                )
            )
        else:
            await run_blocking(
                functools.partial(
                    self.kia_uvo_api.start_climate,
                    self.token,
                    set_temp,
                    duration,
                    defrost,
                    climate,
                    heating,
                )
            )
        await self.force_update_loop_start()

    async def stop_climate(self):
        if (
            self.engine_type == VEHICLE_ENGINE_TYPE.EV
            and REGIONS[self.region] == REGION_CANADA
        ):
            await run_blocking(
                functools.partial(self.kia_uvo_api.stop_climate_ev, self.token)
            )
        else:
            await run_blocking(
                functools.partial(self.kia_uvo_api.stop_climate, self.token)
            )
        await self.force_update_loop_start()

    async def start_charge(self):
        await run_blocking(functools.partial(self.kia_uvo_api.start_charge, self.token))
        await self.force_update_loop_start()

    async def stop_charge(self):
        await run_blocking(functools.partial(self.kia_uvo_api.stop_charge, self.token))
        await self.force_update_loop_start()

    async def set_charge_limits(self, ac_limit: int, dc_limit: int):
        if ac_limit is None:
            ac_limit = 90
        if dc_limit is None:
            dc_limit = 90
        await run_blocking(
            functools.partial(
                self.kia_uvo_api.set_charge_limits, self.token, ac_limit, dc_limit
            )
        )
        await self.force_update_loop_start()

    def login(self):
        self.token = self.kia_uvo_api.login()

    def set_last_updated(self):
        m = re.match(
            r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})",
            self.vehicle_data["vehicleStatus"]["time"],
        )
        local_timezone = self.kia_uvo_api.get_timezone_by_region()
        last_updated = datetime(
            year=int(m.group(1)),
            month=int(m.group(2)),
            day=int(m.group(3)),
            hour=int(m.group(4)),
            minute=int(m.group(5)),
            second=int(m.group(6)),
            tzinfo=local_timezone,
        )

        _LOGGER.debug(f"LastUpdated {last_updated} - Timezone {local_timezone}")

        self.last_updated = last_updated

    def set_engine_type(self):
        if "evStatus" in self.vehicle_data[
            "vehicleStatus"
        ] and self.token.vehicle_model.endswith(" EV"):
            self.engine_type = VEHICLE_ENGINE_TYPE.EV
        else:
            if (
                "evStatus" in self.vehicle_data["vehicleStatus"]
                and "lowFuelLight" in self.vehicle_data["vehicleStatus"]
            ):
                self.engine_type = VEHICLE_ENGINE_TYPE.PHEV
            else:
                if "evStatus" in self.vehicle_data["vehicleStatus"]:
                    self.engine_type = VEHICLE_ENGINE_TYPE.EV
                else:
                    self.engine_type = VEHICLE_ENGINE_TYPE.IC
        _LOGGER.debug(f"Engine type set {self.engine_type}")

    def get_child_value(self, key):
        value = self.vehicle_data
        for x in key.split("."):
            try:
                value = value[x]
            except:
                try:
                    value = value[int(x)]
                except:
                    value = None
        return value