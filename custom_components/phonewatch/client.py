"""Client module for interacting with Sector Alarm API."""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar

import aiohttp
import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONFIG_TO_ENDPOINT_MAP
from .endpoints import get_action_endpoints, get_data_endpoints

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


class AuthenticationError(Exception):
    """Exception raised for authentication errors."""


class TokenRefreshError(Exception):
    """Exception raised for token refresh errors."""


class SectorAlarmAPI:
    """Class to interact with the Sector Alarm API."""

    API_URL = "https://mypagesapi.sectoralarm.net"

    def __init__(self, hass: HomeAssistant, email, password, panel_id, enabled_endpoints=None):
        """Initialize the API client."""
        self.hass = hass
        self.email = email
        self.password = password
        self.panel_id = panel_id
        self.access_token = None
        self.token_expiry = None
        self.headers: dict[str, str] = {}
        self.session = None
        self.data_endpoints = get_data_endpoints(self.panel_id)
        self.action_endpoints = get_action_endpoints()
        self.enabled_endpoints = enabled_endpoints or {}
        # Default retry configuration
        self.max_retries = 3
        self.base_delay = 1.0  # seconds
        # Token management settings
        self.token_lifetime = timedelta(hours=1)  # Conservative estimate, adjust as needed
        self.token_refresh_margin = timedelta(minutes=5)  # Refresh 5 minutes before expiry

    async def _retry_with_backoff(
        self, func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Execute a function with exponential backoff retry logic."""
        retries = 0
        last_exception = None

        while retries <= self.max_retries:
            try:
                return await func(*args, **kwargs)
            except (asyncio.TimeoutError, aiohttp.ClientError) as err:
                last_exception = err
                if retries == self.max_retries:
                    break

                # Calculate delay with exponential backoff and jitter
                delay = min(2 ** retries * self.base_delay, 30)
                jitter = random.uniform(0, 0.1 * delay)
                total_delay = delay + jitter
                
                _LOGGER.warning(
                    "Request failed, retrying in %.2f seconds (retry %d/%d): %s",
                    total_delay,
                    retries + 1,
                    self.max_retries,
                    str(err),
                )
                
                await asyncio.sleep(total_delay)
                retries += 1

        # If we've exhausted all retries, raise the last exception
        if isinstance(last_exception, asyncio.TimeoutError):
            _LOGGER.error("Maximum retries reached, all attempts timed out")
        else:
            _LOGGER.error(
                "Maximum retries reached, last error: %s", str(last_exception)
            )
        raise last_exception

    def _is_token_valid(self) -> bool:
        """Check if the current token is valid and not expired."""
        if not self.access_token or not self.token_expiry:
            return False
        
        # Add a margin to refresh the token before it actually expires
        refresh_time = self.token_expiry - self.token_refresh_margin
        return datetime.now() < refresh_time

    async def ensure_authenticated(self):
        """Ensure we have a valid access token, refreshing or authenticating as needed."""
        if self.session is None:
            self.session = async_get_clientsession(self.hass)
            
        if self._is_token_valid():
            _LOGGER.debug("Using existing token, valid until %s", self.token_expiry)
            return
            
        # We need a new token, perform full authentication
        _LOGGER.debug("Token invalid or expired, authenticating...")
        await self.login()

    async def login(self):
        """Authenticate with the API and obtain an access token."""
        if self.session is None:
            self.session = async_get_clientsession(self.hass)

        login_url = f"{self.API_URL}/api/Login/Login"
        payload = {
            "userId": self.email,
            "password": self.password,
        }
        
        async def _do_login():
            async with async_timeout.timeout(10):
                async with self.session.post(login_url, json=payload) as response:
                    if response.status != 200:
                        _LOGGER.error(
                            "Login failed with status code %s", response.status
                        )
                        raise AuthenticationError("Invalid credentials")
                    data = await response.json()
                    self.access_token = data.get("AuthorizationToken")
                    if not self.access_token:
                        _LOGGER.error("Login failed: No access token received")
                        raise AuthenticationError("Invalid credentials")
                    
                    # Set token expiry time
                    self.token_expiry = datetime.now() + self.token_lifetime
                    _LOGGER.debug("New token obtained, expires at %s", self.token_expiry)
                    
                    self.headers = {
                        "Authorization": f"Bearer {self.access_token}",
                        "Accept": "application/json",
                    }
        
        try:
            # Authentication errors shouldn't be retried, so we don't use retry logic here
            await _do_login()
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout occurred during login")
            raise AuthenticationError("Timeout during login") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Client error during login: %s", str(err))
            raise AuthenticationError("Client error during login") from err

    async def get_panel_list(self) -> dict[str, str]:
        """Retrieve available panels from the API."""
        data = {}
        panellist_url = f"{self.API_URL}/api/account/GetPanelList"
        
        try:
            await self.ensure_authenticated()
            response = await self._retry_with_backoff(self._get, panellist_url)
            _LOGGER.debug(f"panel_payload: {response}")

            if response:
                data = {
                    item["PanelId"]: item["DisplayName"]
                    for item in response
                    if "PanelId" in item
                }
            else:
                _LOGGER.error("Failed to retrieve any panels")
        except Exception as err:
            _LOGGER.error("Error retrieving panel list: %s", str(err))

        return data

    async def retrieve_all_data(self):
        """Retrieve all relevant data from the API."""
        data = {}

        try:
            # Ensure we have a valid authentication token before making requests
            await self.ensure_authenticated()
            
            # Iterate over data endpoints
            for config_option, endpoint_key in CONFIG_TO_ENDPOINT_MAP.items():
                # Skip disabled endpoints
                if config_option in self.enabled_endpoints and not self.enabled_endpoints[config_option]:
                    _LOGGER.debug("Skipping disabled endpoint: %s", endpoint_key)
                    continue
                    
                if endpoint_key not in self.data_endpoints:
                    _LOGGER.debug("Endpoint not found in data_endpoints: %s", endpoint_key)
                    continue
                    
                method, url = self.data_endpoints[endpoint_key]
                    
                try:
                    if method == "GET":
                        response = await self._retry_with_backoff(self._get, url)
                    elif method == "POST":
                        # For POST requests, we need to provide the panel ID in the payload
                        payload = {"PanelId": self.panel_id}
                        response = await self._retry_with_backoff(self._post, url, payload)
                    else:
                        _LOGGER.error("Unsupported HTTP method %s for endpoint %s", method, endpoint_key)
                        continue

                    if response:
                        data[endpoint_key] = response
                    else:
                        _LOGGER.info("No data retrieved for %s", endpoint_key)
                except Exception as err:
                    _LOGGER.error("Error retrieving data for %s: %s", endpoint_key, str(err))

            # Always fetch panel status regardless of settings
            try:
                panel_status_method, panel_status_url = self.data_endpoints["Panel Status"]
                panel_response = await self._retry_with_backoff(self._get, panel_status_url)
                if panel_response:
                    data["Panel Status"] = panel_response
            except Exception as err:
                _LOGGER.error("Error retrieving panel status: %s", str(err))
                
            # Always fetch locks status if not otherwise configured
            lock_key = "Lock Status"
            fetch_locks = True
            if "lock_status" in self.enabled_endpoints:
                fetch_locks = self.enabled_endpoints["lock_status"]
                
            if fetch_locks:
                try:
                    locks_status = await self.get_lock_status()
                    data[lock_key] = locks_status
                except Exception as err:
                    _LOGGER.error("Error retrieving lock status: %s", str(err))
                    data[lock_key] = []

            # Always fetch logs for event handling
            try:
                logs_method, logs_url = self.data_endpoints["Logs"]
                logs_response = await self._retry_with_backoff(self._get, logs_url)
                if logs_response:
                    data["Logs"] = logs_response
            except Exception as err:
                _LOGGER.error("Error retrieving logs: %s", str(err))
                
        except AuthenticationError as err:
            _LOGGER.error("Authentication error during data retrieval: %s", str(err))
            raise
            
        return data

    async def get_lock_status(self):
        """Retrieve the lock status."""
        url = f"{self.API_URL}/api/panel/GetLockStatus?panelId={self.panel_id}"
        try:
            await self.ensure_authenticated()
            response = await self._retry_with_backoff(self._get, url)
            if response:
                return response
            else:
                _LOGGER.error("Failed to retrieve lock status")
                return []
        except Exception as err:
            _LOGGER.error("Error retrieving lock status: %s", str(err))
            return []

    async def _get(self, url):
        """Helper method to perform GET requests with timeout."""
        try:
            async with async_timeout.timeout(10):
                async with self.session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        content_type = response.headers.get("Content-Type", "")
                        if "application/json" in content_type:
                            return await response.json()
                        else:
                            text = await response.text()
                            _LOGGER.error(
                                "Received non-JSON response from %s: %s", url, text
                            )
                            return None
                    elif response.status == 401:
                        _LOGGER.warning("Authorization token expired, will re-authenticate")
                        # Clear token data so we re-authenticate on next call
                        self.access_token = None
                        self.token_expiry = None
                        raise AuthenticationError("Token expired")
                    else:
                        text = await response.text()
                        _LOGGER.error(
                            "GET request to %s failed with status code %s, response: %s",
                            url,
                            response.status,
                            text,
                        )
                        return None
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout occurred during GET request to %s", url)
            raise
        except aiohttp.ClientError as e:
            _LOGGER.error("Client error during GET request to %s: %s", url, str(e))
            raise

    async def _post(self, url, payload):
        """Helper method to perform POST requests with timeout."""
        try:
            async with async_timeout.timeout(10):
                async with self.session.post(
                    url, json=payload, headers=self.headers
                ) as response:
                    if response.status == 200:
                        content_type = response.headers.get("Content-Type", "")
                        if "application/json" in content_type:
                            return await response.json()
                        else:
                            text = await response.text()
                            _LOGGER.error(
                                "Received non-JSON response from %s: %s", url, text
                            )
                            return None
                    elif response.status == 401:
                        _LOGGER.warning("Authorization token expired, will re-authenticate")
                        # Clear token data so we re-authenticate on next call
                        self.access_token = None
                        self.token_expiry = None
                        raise AuthenticationError("Token expired")
                    else:
                        text = await response.text()
                        _LOGGER.error(
                            "POST request to %s failed with status code %s, response: %s",
                            url,
                            response.status,
                            text,
                        )
                        return None
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout occurred during POST request to %s", url)
            raise
        except aiohttp.ClientError as err:
            _LOGGER.error("Client error during POST request to %s: %s", url, str(err))
            raise

    async def arm_system(self, mode: str, code: str):
        """Arm the alarm system."""
        panel_code = code
        if mode == "total":
            url = self.action_endpoints["Arm"][1]
        elif mode == "partial":
            url = self.action_endpoints["PartialArm"][1]

        payload = {
            "PanelCode": panel_code,
            "PanelId": self.panel_id,
        }
        
        try:
            await self.ensure_authenticated()
            result = await self._retry_with_backoff(self._post, url, payload)
            if result is not None:
                _LOGGER.debug("System armed successfully")
                return True
            else:
                _LOGGER.error("Failed to arm system")
                return False
        except Exception as err:
            _LOGGER.error("Error while arming system: %s", str(err))
            return False

    async def disarm_system(self, code: str):
        """Disarm the alarm system."""
        panel_code = code
        url = self.action_endpoints["Disarm"][1]
        payload = {
            "PanelCode": panel_code,
            "PanelId": self.panel_id,
        }
        
        try:
            await self.ensure_authenticated()
            result = await self._retry_with_backoff(self._post, url, payload)
            if result is not None:
                _LOGGER.debug("System disarmed successfully")
                return True
            else:
                _LOGGER.error("Failed to disarm system")
                return False
        except Exception as err:
            _LOGGER.error("Error while disarming system: %s", str(err))
            return False

    async def lock_door(self, serial_no: str, code: str):
        """Lock a specific door."""
        panel_code = code
        url = self.action_endpoints["Lock"][1]
        payload = {
            "LockSerial": serial_no,
            "PanelCode": panel_code,
            "PanelId": self.panel_id,
            "SerialNo": serial_no,
        }
        
        try:
            await self.ensure_authenticated()
            result = await self._retry_with_backoff(self._post, url, payload)
            if result is not None:
                _LOGGER.debug("Door %s locked successfully", serial_no)
                return True
            else:
                _LOGGER.error("Failed to lock door %s", serial_no)
                return False
        except Exception as err:
            _LOGGER.error("Error while locking door %s: %s", serial_no, str(err))
            return False

    async def unlock_door(self, serial_no: str, code: str):
        """Unlock a specific door."""
        panel_code = code
        url = self.action_endpoints["Unlock"][1]
        payload = {
            "LockSerial": serial_no,
            "PanelCode": panel_code,
            "PanelId": self.panel_id,
            "SerialNo": serial_no,
        }
        
        try:
            await self.ensure_authenticated()
            result = await self._retry_with_backoff(self._post, url, payload)
            if result is not None:
                _LOGGER.debug("Door %s unlocked successfully", serial_no)
                return True
            else:
                _LOGGER.error("Failed to unlock door %s", serial_no)
                return False
        except Exception as err:
            _LOGGER.error("Error while unlocking door %s: %s", serial_no, str(err))
            return False

    async def turn_on_smartplug(self, plug_id):
        """Turn on a specific smart plug."""
        url = self.action_endpoints["TurnOnSmartplug"][1]
        payload = {
            "PanelId": self.panel_id,
            "SmartplugId": plug_id,
        }
        
        try:
            await self.ensure_authenticated()
            result = await self._retry_with_backoff(self._post, url, payload)
            if result is not None:
                _LOGGER.debug("Smart plug %s turned on successfully", plug_id)
                return True
            else:
                _LOGGER.error("Failed to turn on smart plug %s", plug_id)
                return False
        except Exception as err:
            _LOGGER.error("Error while turning on smart plug %s: %s", plug_id, str(err))
            return False

    async def turn_off_smartplug(self, plug_id):
        """Turn off a specific smart plug."""
        url = self.action_endpoints["TurnOffSmartplug"][1]
        payload = {
            "PanelId": self.panel_id,
            "SmartplugId": plug_id,
        }
        
        try:
            await self.ensure_authenticated()
            result = await self._retry_with_backoff(self._post, url, payload)
            if result is not None:
                _LOGGER.debug("Smart plug %s turned off successfully", plug_id)
                return True
            else:
                _LOGGER.error("Failed to turn off smart plug %s", plug_id)
                return False
        except Exception as err:
            _LOGGER.error("Error while turning off smart plug %s: %s", plug_id, str(err))
            return False

    async def get_camera_image(self, serial_no):
        """Get a camera image."""
        url = f"{self.API_URL}/api/panel/GetCameraImage"
        payload = {
            "PanelId": self.panel_id,
            "CameraId": serial_no,
        }
        
        try:
            await self.ensure_authenticated()
            async with async_timeout.timeout(20):  # Longer timeout for image retrieval
                async with self.session.post(
                    url, json=payload, headers=self.headers
                ) as response:
                    if response.status == 200:
                        content_type = response.headers.get("Content-Type", "")
                        if "image/" in content_type or "application/octet-stream" in content_type:
                            return await response.read()
                        else:
                            _LOGGER.error(
                                "Received unexpected content type: %s", content_type
                            )
                            return None
                    elif response.status == 401:
                        _LOGGER.warning("Authorization token expired, will re-authenticate")
                        # Clear token data so we re-authenticate on next call
                        self.access_token = None
                        self.token_expiry = None
                        raise AuthenticationError("Token expired")
                    else:
                        _LOGGER.error(
                            "Failed to get camera image with status code %s", response.status
                        )
                        return None
        except Exception as err:
            _LOGGER.error("Error while retrieving camera image: %s", str(err))
            return None

    async def logout(self):
        """Log out from the API."""
        # Clear token data
        self.access_token = None
        self.token_expiry = None
        self.headers = {}
        _LOGGER.debug("Logged out from Sector Alarm API")
        return True
