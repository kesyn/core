"""Offer geolocation automation rules."""
import voluptuous as vol

from homeassistant.components.geo_location import DOMAIN
from homeassistant.const import CONF_EVENT, CONF_PLATFORM, CONF_SOURCE, CONF_ZONE
from homeassistant.core import HassJob, callback
from homeassistant.helpers import condition, config_validation as cv
from homeassistant.helpers.config_validation import entity_domain
from homeassistant.helpers.event import TrackStates, async_track_state_change_filtered

# mypy: allow-untyped-defs, no-check-untyped-defs

EVENT_ENTER = "enter"
EVENT_LEAVE = "leave"
DEFAULT_EVENT = EVENT_ENTER

TRIGGER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PLATFORM): "geo_location",
        vol.Required(CONF_SOURCE): cv.string,
        vol.Required(CONF_ZONE): entity_domain("zone"),
        vol.Required(CONF_EVENT, default=DEFAULT_EVENT): vol.Any(
            EVENT_ENTER, EVENT_LEAVE
        ),
    }
)


def source_match(state, source):
    """Check if the state matches the provided source."""
    return state and state.attributes.get("source") == source


async def async_attach_trigger(hass, config, action, automation_info):
    """Listen for state changes based on configuration."""
    trigger_id = automation_info.get("trigger_id") if automation_info else None
    source = config.get(CONF_SOURCE).lower()
    zone_entity_id = config.get(CONF_ZONE)
    trigger_event = config.get(CONF_EVENT)
    job = HassJob(action)

    @callback
    def state_change_listener(event):
        """Handle specific state changes."""
        # Skip if the event's source does not match the trigger's source.
        from_state = event.data.get("old_state")
        to_state = event.data.get("new_state")
        if not source_match(from_state, source) and not source_match(to_state, source):
            return

        zone_state = hass.states.get(zone_entity_id)

        from_match = (
            condition.zone(hass, zone_state, from_state) if from_state else False
        )
        to_match = condition.zone(hass, zone_state, to_state) if to_state else False

        if (
            trigger_event == EVENT_ENTER
            and not from_match
            and to_match
            or trigger_event == EVENT_LEAVE
            and from_match
            and not to_match
        ):
            hass.async_run_hass_job(
                job,
                {
                    "trigger": {
                        "platform": "geo_location",
                        "source": source,
                        "entity_id": event.data.get("entity_id"),
                        "from_state": from_state,
                        "to_state": to_state,
                        "zone": zone_state,
                        "event": trigger_event,
                        "description": f"geo_location - {source}",
                        "id": trigger_id,
                    }
                },
                event.context,
            )

    return async_track_state_change_filtered(
        hass, TrackStates(False, set(), {DOMAIN}), state_change_listener
    ).async_remove
