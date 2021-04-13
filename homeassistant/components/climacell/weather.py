"""Weather component that handles meteorological data for your location."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Callable, Mapping

from pyclimacell.const import (
    CURRENT,
    DAILY,
    FORECASTS,
    HOURLY,
    NOWCAST,
    PrecipitationType,
    WeatherCode,
)

from homeassistant.components.weather import (
    ATTR_FORECAST_CONDITION,
    ATTR_FORECAST_PRECIPITATION,
    ATTR_FORECAST_PRECIPITATION_PROBABILITY,
    ATTR_FORECAST_TEMP,
    ATTR_FORECAST_TEMP_LOW,
    ATTR_FORECAST_TIME,
    ATTR_FORECAST_WIND_BEARING,
    ATTR_FORECAST_WIND_SPEED,
    WeatherEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_VERSION,
    LENGTH_FEET,
    LENGTH_KILOMETERS,
    LENGTH_METERS,
    LENGTH_MILES,
    PRESSURE_HPA,
    PRESSURE_INHG,
    TEMP_FAHRENHEIT,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.sun import is_up
from homeassistant.helpers.typing import HomeAssistantType
from homeassistant.util import dt as dt_util
from homeassistant.util.distance import convert as distance_convert
from homeassistant.util.pressure import convert as pressure_convert

from . import ClimaCellEntity
from .const import (
    ATTR_CLOUD_COVER,
    ATTR_PRECIPITATION_TYPE,
    ATTR_WIND_GUST,
    CC_ATTR_CLOUD_COVER,
    CC_ATTR_CONDITION,
    CC_ATTR_HUMIDITY,
    CC_ATTR_OZONE,
    CC_ATTR_PRECIPITATION,
    CC_ATTR_PRECIPITATION_PROBABILITY,
    CC_ATTR_PRECIPITATION_TYPE,
    CC_ATTR_PRESSURE,
    CC_ATTR_TEMPERATURE,
    CC_ATTR_TEMPERATURE_HIGH,
    CC_ATTR_TEMPERATURE_LOW,
    CC_ATTR_TIMESTAMP,
    CC_ATTR_VISIBILITY,
    CC_ATTR_WIND_DIRECTION,
    CC_ATTR_WIND_GUST,
    CC_ATTR_WIND_SPEED,
    CC_V3_ATTR_CLOUD_COVER,
    CC_V3_ATTR_CONDITION,
    CC_V3_ATTR_HUMIDITY,
    CC_V3_ATTR_OZONE,
    CC_V3_ATTR_PRECIPITATION,
    CC_V3_ATTR_PRECIPITATION_DAILY,
    CC_V3_ATTR_PRECIPITATION_PROBABILITY,
    CC_V3_ATTR_PRECIPITATION_TYPE,
    CC_V3_ATTR_PRESSURE,
    CC_V3_ATTR_TEMPERATURE,
    CC_V3_ATTR_TEMPERATURE_HIGH,
    CC_V3_ATTR_TEMPERATURE_LOW,
    CC_V3_ATTR_TIMESTAMP,
    CC_V3_ATTR_VISIBILITY,
    CC_V3_ATTR_WIND_DIRECTION,
    CC_V3_ATTR_WIND_GUST,
    CC_V3_ATTR_WIND_SPEED,
    CLEAR_CONDITIONS,
    CONDITIONS,
    CONDITIONS_V3,
    CONF_TIMESTEP,
    DOMAIN,
    MAX_FORECASTS,
)

# mypy: allow-untyped-defs, no-check-untyped-defs

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistantType,
    config_entry: ConfigEntry,
    async_add_entities: Callable[[list[Entity], bool], None],
) -> None:
    """Set up a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    api_version = config_entry.data[CONF_API_VERSION]

    api_class = ClimaCellV3WeatherEntity if api_version == 3 else ClimaCellWeatherEntity
    entities = [
        api_class(config_entry, coordinator, forecast_type, api_version)
        for forecast_type in [DAILY, HOURLY, NOWCAST]
    ]
    async_add_entities(entities)


class BaseClimaCellWeatherEntity(ClimaCellEntity, WeatherEntity):
    """Base ClimaCell weather entity."""

    @staticmethod
    def _translate_condition(
        condition: int | None, sun_is_up: bool = True
    ) -> str | None:
        """Translate ClimaCell condition into an HA condition."""
        raise NotImplementedError()

    def _forecast_dict(
        self,
        forecast_dt: datetime,
        use_datetime: bool,
        condition: str,
        precipitation: float | None,
        precipitation_probability: float | None,
        temp: float | None,
        temp_low: float | None,
        wind_direction: float | None,
        wind_speed: float | None,
    ) -> dict[str, Any]:
        """Return formatted Forecast dict from ClimaCell forecast data."""
        if use_datetime:
            translated_condition = self._translate_condition(
                condition, is_up(self.hass, forecast_dt)
            )
        else:
            translated_condition = self._translate_condition(condition, True)

        if self.hass.config.units.is_metric:
            if precipitation:
                precipitation = (
                    distance_convert(precipitation / 12, LENGTH_FEET, LENGTH_METERS)
                    * 1000
                )
            if wind_speed:
                wind_speed = distance_convert(
                    wind_speed, LENGTH_MILES, LENGTH_KILOMETERS
                )

        data = {
            ATTR_FORECAST_TIME: forecast_dt.isoformat(),
            ATTR_FORECAST_CONDITION: translated_condition,
            ATTR_FORECAST_PRECIPITATION: precipitation,
            ATTR_FORECAST_PRECIPITATION_PROBABILITY: precipitation_probability,
            ATTR_FORECAST_TEMP: temp,
            ATTR_FORECAST_TEMP_LOW: temp_low,
            ATTR_FORECAST_WIND_BEARING: wind_direction,
            ATTR_FORECAST_WIND_SPEED: wind_speed,
        }

        return {k: v for k, v in data.items() if v is not None}

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return additional state attributes."""
        wind_gust = self.wind_gust
        if wind_gust and self.hass.config.units.is_metric:
            wind_gust = distance_convert(
                self.wind_gust, LENGTH_MILES, LENGTH_KILOMETERS
            )
        cloud_cover = self.cloud_cover
        if cloud_cover is not None:
            cloud_cover /= 100
        return {
            ATTR_CLOUD_COVER: cloud_cover,
            ATTR_WIND_GUST: wind_gust,
            ATTR_PRECIPITATION_TYPE: self.precipitation_type,
        }

    @property
    def cloud_cover(self):
        """Return cloud cover."""
        raise NotImplementedError

    @property
    def wind_gust(self):
        """Return wind gust speed."""
        raise NotImplementedError

    @property
    def precipitation_type(self):
        """Return precipitation type."""
        raise NotImplementedError


class ClimaCellWeatherEntity(BaseClimaCellWeatherEntity):
    """Entity that talks to ClimaCell v4 API to retrieve weather data."""

    @staticmethod
    def _translate_condition(
        condition: int | None, sun_is_up: bool = True
    ) -> str | None:
        """Translate ClimaCell condition into an HA condition."""
        if condition is None:
            return None
        # We won't guard here, instead we will fail hard
        condition = WeatherCode(condition)
        if condition in (WeatherCode.CLEAR, WeatherCode.MOSTLY_CLEAR):
            if sun_is_up:
                return CLEAR_CONDITIONS["day"]
            return CLEAR_CONDITIONS["night"]
        return CONDITIONS[condition]

    def _get_current_property(self, property_name: str) -> int | str | float | None:
        """Get property from current conditions."""
        return self.coordinator.data.get(CURRENT, {}).get(property_name)

    @property
    def temperature(self):
        """Return the platform temperature."""
        return self._get_current_property(CC_ATTR_TEMPERATURE)

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return TEMP_FAHRENHEIT

    @property
    def pressure(self):
        """Return the pressure."""
        pressure = self._get_current_property(CC_ATTR_PRESSURE)
        if self.hass.config.units.is_metric and pressure:
            return pressure_convert(pressure, PRESSURE_INHG, PRESSURE_HPA)
        return pressure

    @property
    def humidity(self):
        """Return the humidity."""
        return self._get_current_property(CC_ATTR_HUMIDITY)

    @property
    def wind_gust(self):
        """Return the wind gust speed."""
        return self._get_current_property(CC_ATTR_WIND_GUST)

    @property
    def cloud_cover(self):
        """Reteurn the cloud cover."""
        return self._get_current_property(CC_ATTR_CLOUD_COVER)

    @property
    def precipitation_type(self):
        """Return precipitation type."""
        precipitation_type = self._get_current_property(CC_ATTR_PRECIPITATION_TYPE)
        if precipitation_type is None:
            return None
        return PrecipitationType(precipitation_type).name.lower()

    @property
    def wind_speed(self):
        """Return the wind speed."""
        wind_speed = self._get_current_property(CC_ATTR_WIND_SPEED)
        if self.hass.config.units.is_metric and wind_speed:
            return distance_convert(wind_speed, LENGTH_MILES, LENGTH_KILOMETERS)
        return wind_speed

    @property
    def wind_bearing(self):
        """Return the wind bearing."""
        return self._get_current_property(CC_ATTR_WIND_DIRECTION)

    @property
    def ozone(self):
        """Return the O3 (ozone) level."""
        return self._get_current_property(CC_ATTR_OZONE)

    @property
    def condition(self):
        """Return the condition."""
        return self._translate_condition(
            self._get_current_property(CC_ATTR_CONDITION),
            is_up(self.hass),
        )

    @property
    def visibility(self):
        """Return the visibility."""
        visibility = self._get_current_property(CC_ATTR_VISIBILITY)
        if self.hass.config.units.is_metric and visibility:
            return distance_convert(visibility, LENGTH_MILES, LENGTH_KILOMETERS)
        return visibility

    @property
    def forecast(self):
        """Return the forecast."""
        # Check if forecasts are available
        raw_forecasts = self.coordinator.data.get(FORECASTS, {}).get(self.forecast_type)
        if not raw_forecasts:
            return None

        forecasts = []
        max_forecasts = MAX_FORECASTS[self.forecast_type]
        forecast_count = 0

        # Set default values (in cases where keys don't exist), None will be
        # returned. Override properties per forecast type as needed
        for forecast in raw_forecasts:
            forecast_dt = dt_util.parse_datetime(forecast[CC_ATTR_TIMESTAMP])

            # Throw out past data
            if forecast_dt.date() < dt_util.utcnow().date():
                continue

            values = forecast["values"]
            use_datetime = True

            condition = values.get(CC_ATTR_CONDITION)
            precipitation = values.get(CC_ATTR_PRECIPITATION)
            precipitation_probability = values.get(CC_ATTR_PRECIPITATION_PROBABILITY)

            temp = values.get(CC_ATTR_TEMPERATURE_HIGH)
            temp_low = values.get(CC_ATTR_TEMPERATURE_LOW)
            wind_direction = values.get(CC_ATTR_WIND_DIRECTION)
            wind_speed = values.get(CC_ATTR_WIND_SPEED)

            if self.forecast_type == DAILY:
                use_datetime = False
                if precipitation:
                    precipitation = precipitation * 24
            elif self.forecast_type == NOWCAST:
                # Precipitation is forecasted in CONF_TIMESTEP increments but in a
                # per hour rate, so value needs to be converted to an amount.
                if precipitation:
                    precipitation = (
                        precipitation / 60 * self._config_entry.options[CONF_TIMESTEP]
                    )

            forecasts.append(
                self._forecast_dict(
                    forecast_dt,
                    use_datetime,
                    condition,
                    precipitation,
                    precipitation_probability,
                    temp,
                    temp_low,
                    wind_direction,
                    wind_speed,
                )
            )

            forecast_count += 1
            if forecast_count == max_forecasts:
                break

        return forecasts


class ClimaCellV3WeatherEntity(BaseClimaCellWeatherEntity):
    """Entity that talks to ClimaCell v3 API to retrieve weather data."""

    @staticmethod
    def _translate_condition(
        condition: str | None, sun_is_up: bool = True
    ) -> str | None:
        """Translate ClimaCell condition into an HA condition."""
        if not condition:
            return None
        if "clear" in condition.lower():
            if sun_is_up:
                return CLEAR_CONDITIONS["day"]
            return CLEAR_CONDITIONS["night"]
        return CONDITIONS_V3[condition]

    @property
    def temperature(self):
        """Return the platform temperature."""
        return self._get_cc_value(
            self.coordinator.data[CURRENT], CC_V3_ATTR_TEMPERATURE
        )

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return TEMP_FAHRENHEIT

    @property
    def pressure(self):
        """Return the pressure."""
        pressure = self._get_cc_value(
            self.coordinator.data[CURRENT], CC_V3_ATTR_PRESSURE
        )
        if self.hass.config.units.is_metric and pressure:
            return pressure_convert(pressure, PRESSURE_INHG, PRESSURE_HPA)
        return pressure

    @property
    def humidity(self):
        """Return the humidity."""
        return self._get_cc_value(self.coordinator.data[CURRENT], CC_V3_ATTR_HUMIDITY)

    @property
    def wind_gust(self):
        """Return the wind gust speed."""
        return self._get_cc_value(self.coordinator.data[CURRENT], CC_V3_ATTR_WIND_GUST)

    @property
    def cloud_cover(self):
        """Reteurn the cloud cover."""
        return self._get_cc_value(
            self.coordinator.data[CURRENT], CC_V3_ATTR_CLOUD_COVER
        )

    @property
    def precipitation_type(self):
        """Return precipitation type."""
        return self._get_cc_value(
            self.coordinator.data[CURRENT], CC_V3_ATTR_PRECIPITATION_TYPE
        )

    @property
    def wind_speed(self):
        """Return the wind speed."""
        wind_speed = self._get_cc_value(
            self.coordinator.data[CURRENT], CC_V3_ATTR_WIND_SPEED
        )
        if self.hass.config.units.is_metric and wind_speed:
            return distance_convert(wind_speed, LENGTH_MILES, LENGTH_KILOMETERS)
        return wind_speed

    @property
    def wind_bearing(self):
        """Return the wind bearing."""
        return self._get_cc_value(
            self.coordinator.data[CURRENT], CC_V3_ATTR_WIND_DIRECTION
        )

    @property
    def ozone(self):
        """Return the O3 (ozone) level."""
        return self._get_cc_value(self.coordinator.data[CURRENT], CC_V3_ATTR_OZONE)

    @property
    def condition(self):
        """Return the condition."""
        return self._translate_condition(
            self._get_cc_value(self.coordinator.data[CURRENT], CC_V3_ATTR_CONDITION),
            is_up(self.hass),
        )

    @property
    def visibility(self):
        """Return the visibility."""
        visibility = self._get_cc_value(
            self.coordinator.data[CURRENT], CC_V3_ATTR_VISIBILITY
        )
        if self.hass.config.units.is_metric and visibility:
            return distance_convert(visibility, LENGTH_MILES, LENGTH_KILOMETERS)
        return visibility

    @property
    def forecast(self):
        """Return the forecast."""
        # Check if forecasts are available
        raw_forecasts = self.coordinator.data.get(FORECASTS, {}).get(self.forecast_type)
        if not raw_forecasts:
            return None

        forecasts = []

        # Set default values (in cases where keys don't exist), None will be
        # returned. Override properties per forecast type as needed
        for forecast in raw_forecasts:
            forecast_dt = dt_util.parse_datetime(
                self._get_cc_value(forecast, CC_V3_ATTR_TIMESTAMP)
            )
            use_datetime = True
            condition = self._get_cc_value(forecast, CC_V3_ATTR_CONDITION)
            precipitation = self._get_cc_value(forecast, CC_V3_ATTR_PRECIPITATION)
            precipitation_probability = self._get_cc_value(
                forecast, CC_V3_ATTR_PRECIPITATION_PROBABILITY
            )
            temp = self._get_cc_value(forecast, CC_V3_ATTR_TEMPERATURE)
            temp_low = None
            wind_direction = self._get_cc_value(forecast, CC_V3_ATTR_WIND_DIRECTION)
            wind_speed = self._get_cc_value(forecast, CC_V3_ATTR_WIND_SPEED)

            if self.forecast_type == DAILY:
                use_datetime = False
                forecast_dt = dt_util.start_of_local_day(forecast_dt)
                precipitation = self._get_cc_value(
                    forecast, CC_V3_ATTR_PRECIPITATION_DAILY
                )
                temp = next(
                    (
                        self._get_cc_value(item, CC_V3_ATTR_TEMPERATURE_HIGH)
                        for item in forecast[CC_V3_ATTR_TEMPERATURE]
                        if "max" in item
                    ),
                    temp,
                )
                temp_low = next(
                    (
                        self._get_cc_value(item, CC_V3_ATTR_TEMPERATURE_LOW)
                        for item in forecast[CC_V3_ATTR_TEMPERATURE]
                        if "min" in item
                    ),
                    temp_low,
                )
            elif self.forecast_type == NOWCAST and precipitation:
                # Precipitation is forecasted in CONF_TIMESTEP increments but in a
                # per hour rate, so value needs to be converted to an amount.
                precipitation = (
                    precipitation / 60 * self._config_entry.options[CONF_TIMESTEP]
                )

            forecasts.append(
                self._forecast_dict(
                    forecast_dt,
                    use_datetime,
                    condition,
                    precipitation,
                    precipitation_probability,
                    temp,
                    temp_low,
                    wind_direction,
                    wind_speed,
                )
            )

        return forecasts
