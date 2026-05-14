#!/usr/bin/env python

# This program integrates an external transfer switch ahead of the single AC input
# of a MultiPlus or Quattro inverter/charger.
#
# When the external transfer switch changes between grid and generator the data for
# that input must be switched between grid and generator settings.
#
# These two sets of settings are stored in dbus Settings.
# When the transfer switch digital input changes, this program switches
# the Multiplus settings between these two stored values.
#
# When the user changes the settings, the grid or generator-specific
# Settings are updated.
#
# In order to function, one of the digital inputs must be set to
# Bilge Pump (NOT bilge alarm) and the custom name changed to
# 'Transfer Switch'
#
# This input should be connected to a contact closure on the
# external transfer switch to indicate which source is active.
#
# For Quattro, the /Settings/TransferSwitch/TransferSwitchOnAc2
# tells this program where the transfer switch is connected:
#
#    0 if connected to AC 1 In
#    1 if connected to AC 2 In
#
# credit given to Kevin Windrem for the original package,
# from which this package is based upon.

import platform
import argparse
import logging
import sys
import subprocess
import os
import time
import dbus

from gi.repository import GLib

sys.path.insert(
    1,
    "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python"
)

from vedbus import VeDbusService
from ve_utils import wrap_dbus_value
from settingsdevice import SettingsDevice

# setup logging
logger = logging.getLogger()

for handler in logger.handlers[:]:
    logger.removeHandler(handler)

formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

dbusSettingsPath = "com.victronenergy.settings"
dbusSystemPath = "com.victronenergy.system"


class Monitor:

    def discover_vebus_service(self):
        """Discover and validate VE.Bus service"""
        vebusService = ""

        try:
            obj = self.theBus.get_object(
                dbusSystemPath,
                '/VebusService'
            )
            vebusService = obj.GetText()
        except dbus.exceptions.DBusException as e:
            if self.dbusOk:
                logging.info(
                    "Multi/Quattro disappeared - "
                    "/VebusService invalid: %s",
                    e
                )
            elif not self.veBusFoundInitially and not self.loggedVeBusInitialNotFound:
                logging.warning(
                    "Multi/Quattro (VE.Bus) service not found "
                    "on startup: %s",
                    e
                )
                self.loggedVeBusInitialNotFound = True
        except Exception as e:
            logging.error(
                "Unexpected error while looking for VE.Bus service: %s",
                e
            )

        return vebusService

    def configure_ac_inputs(self, vebusService):
        """Configure AC inputs based on discovered service"""
        if vebusService == "---":
            if self.veBusService != "":
                logging.info("Multi/Quattro disappeared")
            self.veBusService = ""
            self.dbusOk = False
            self.numberOfAcInputs = 0
            return False

        if self.veBusService == "" or vebusService != self.veBusService:
            self.veBusService = vebusService

            try:
                self.numberOfAcInputs = self.theBus.get_object(
                    vebusService,
                    "/Ac/NumberOfAcInputs"
                ).GetValue()
                self.veBusFoundInitially = True
                self.loggedVeBusInitialNotFound = False
            except Exception as e:
                logging.error(
                    "Failed to get /Ac/NumberOfAcInputs: %s",
                    e
                )
                self.numberOfAcInputs = 0
                self.veBusFoundInitially = False
                return False

            try:
                self.remoteGeneratorSelectedItem = self.theBus.get_object(
                    vebusService,
                    "/Ac/Control/RemoteGeneratorSelected"
                )
            except Exception as e:
                logging.error(
                    "Failed to get /Ac/Control/RemoteGeneratorSelected: %s",
                    e
                )
                self.remoteGeneratorSelectedItem = None
                self.remoteGeneratorSelectedLocalValue = -1

            if self.numberOfAcInputs == 0:
                self.dbusOk = False
                self.veBusFoundInitially = False
                return False
            elif self.numberOfAcInputs == 2:
                logging.info("discovered Quattro at " + vebusService)
            elif self.numberOfAcInputs == 1:
                logging.info("discovered Multi at " + vebusService)

            try:
                self.currentLimitObj = self.theBus.get_object(
                    vebusService,
                    "/Ac/ActiveIn/CurrentLimit"
                )
                self.currentLimitIsAdjustableObj = self.theBus.get_object(
                    vebusService,
                    "/Ac/ActiveIn/CurrentLimitIsAdjustable"
                )
                self.ignoreAcIn1Obj = self.theBus.get_object(
                    vebusService,
                    "/Ac/Control/IgnoreAcIn1"
                )
            except Exception as e:
                logging.error(
                    "Failed to get VE.Bus control objects: %s",
                    e
                )
                self.dbusOk = False
                self.veBusFoundInitially = False
                return False

        return True

    def setup_control_objects(self):
        """Setup control objects based on transfer switch location"""
        # determine transfer switch location (cached value check)
        if self.numberOfAcInputs == 0:
            transferSwitchLocation = 0
        elif self.numberOfAcInputs == 1:
            transferSwitchLocation = 1
        elif self.DbusSettings['transferSwitchOnAc2'] == 1:
            transferSwitchLocation = 2
        else:
            transferSwitchLocation = 1

        # refresh object pointers if changed
        if transferSwitchLocation != self.transferSwitchLocation:
            if transferSwitchLocation != 0:
                logging.info(
                    "Transfer switch is on AC %d in"
                    % transferSwitchLocation
                )
            self.transferSwitchLocation = transferSwitchLocation

            try:
                if self.transferSwitchLocation == 2:
                    self.acInputTypeObj = self.theBus.get_object(
                        dbusSettingsPath,
                        "/Settings/SystemSetup/AcInput2"
                    )
                else:
                    self.acInputTypeObj = self.theBus.get_object(
                        dbusSettingsPath,
                        "/Settings/SystemSetup/AcInput1"
                    )
                self.dbusOk = True
                return True
            except Exception as e:
                self.dbusOk = False
                logging.error(
                    "AC input dbus setup failed: %s",
                    e
                )
                return False
        return True

    def getVeBusObjects(self):
        """Main entry point for VE.Bus object discovery and configuration"""
        if not self.transferSwitchActive:
            self.invalidate_dbus_objects()
            return

        vebusService = self.discover_vebus_service()
        
        if not self.configure_ac_inputs(vebusService):
            return
            
        if not self.setup_control_objects():
            return

    def invalidate_dbus_objects(self):
        """Invalidate all DBus objects when transfer switch is inactive"""
        try:
            if self.remoteGeneratorSelectedItem is not None:
                self.remoteGeneratorSelectedItem.SetValue(
                    wrap_dbus_value(0)
                )
        except:
            logging.error(
                "could not release /Ac/Control/RemoteGeneratorSelected"
            )

        self.remoteGeneratorSelectedItem = None
        self.remoteGeneratorSelectedLocalValue = -1
        self.dbusOk = False
        self.numberOfAcInputs = 0
        self.acInputTypeObj = None
        self.veBusService = ""
        self.transferSwitchLocation = 0
        self.veBusFoundInitially = False
        self.loggedVeBusInitialNotFound = False

    def updateTransferSwitchState(self):
        """Update transfer switch state with debouncing and retry logic"""
        inputValid = False
        retry_count = 0
        max_retries = 3
        new_onGenerator = False

        while retry_count < max_retries and not inputValid:
            if self.transferSwitchActive and self.transferSwitchNameObj:
                try:
                    name = self.transferSwitchNameObj.GetValue()
                    if self.extTransferDigInputName.lower() in name.lower():
                        state = self.transferSwitchStateObj.GetValue()
                        if state in (12, 3):
                            inputValid = True
                            new_onGenerator = True
                        elif state in (13, 2):
                            inputValid = True
                            new_onGenerator = False
                    else:
                        logging.info(
                            "Current transfer switch input name '%s' "
                            "does not match '%s'",
                            name,
                            self.extTransferDigInputName
                        )
                        break  # Name mismatch, no point retrying
                except dbus.exceptions.DBusException as e:
                    logging.warning(
                        "DBus error accessing transfer switch (retry %d/%d): %s",
                        retry_count + 1, max_retries, e
                    )
                    retry_count += 1
                    if retry_count < max_retries:
                        time.sleep(0.5)  # Small delay between retries
                    continue
                except Exception as e:
                    logging.error(
                        "Error accessing transfer switch D-Bus object: %s",
                        e
                    )
                    break
            break  # Exit loop if conditions not met

        if not inputValid and self.transferSwitchActive:
            logging.info(
                "Transfer switch digital input no longer valid "
                "or name mismatch"
            )
            self.transferSwitchActive = False
            self.transferSwitchNameObj = None

        elif not inputValid and self.tsInputSearchDelay >= 10:
            self.search_for_transfer_switch_input()

        if self.transferSwitchActive:
            self.tsInputSearchDelay = 0
        else:
            new_onGenerator = False
            if self.tsInputSearchDelay < 10:
                self.tsInputSearchDelay += 1
            else:
                self.tsInputSearchDelay = 0

        # Apply debouncing if state changed
        if inputValid and self.transferSwitchActive:
            # If state changed
            if new_onGenerator != self.onGenerator:
                # If we already have a pending change, cancel it
                if self.debounce_timer:
                    GLib.source_remove(self.debounce_timer)
                    self.debounce_timer = None
                
                # Store pending state
                self.pending_generator_state = new_onGenerator
                
                # Set timer for debounce (500ms)
                self.debounce_timer = GLib.timeout_add(
                    500,  # 0.5 second debounce
                    self.apply_debounced_state
                )
                
                logging.info(f"State change detected: {'generator' if new_onGenerator else 'grid'} - debouncing")
            else:
                # State stable, clear any pending
                if self.debounce_timer:
                    GLib.source_remove(self.debounce_timer)
                    self.debounce_timer = None
                    self.pending_generator_state = None

    def apply_debounced_state(self):
        """Apply the debounced state change"""
        if self.pending_generator_state is not None:
            old_state = self.onGenerator
            self.onGenerator = self.pending_generator_state
            
            # Log only if actually changed
            if old_state != self.onGenerator:
                logging.info(f"Debounced state confirmed: switching to {'generator' if self.onGenerator else 'grid'}")
            
            self.pending_generator_state = None
            self.debounce_timer = None
        
        return False  # Don't repeat

    def search_for_transfer_switch_input(self):
        """Search for transfer switch digital input"""
        newInputService = ""
        custom_name = ""
        found = False

        for service in self.theBus.list_names():
            if service.startswith("com.victronenergy.digitalinput"):
                try:
                    name_obj = self.theBus.get_object(
                        service,
                        '/CustomName'
                    )
                    custom_name_val = name_obj.GetValue()
                    if self.extTransferDigInputName.lower() in custom_name_val.lower():
                        state_obj = self.theBus.get_object(
                            service,
                            '/State'
                        )
                        state = state_obj.GetValue()
                        if state in (12, 3) or state in (13, 2):
                            newInputService = service
                            custom_name = custom_name_val
                            self.transferSwitchNameObj = name_obj
                            self.transferSwitchStateObj = state_obj
                            found = True
                            break
                except:
                    pass

        if found:
            logging.info(
                "discovered transfer switch digital input "
                "service at %s with custom name '%s'",
                newInputService,
                custom_name
            )
            self.transferSwitchActive = True
            self.firstSearchDone = True
        else:
            if self.transferSwitchActive:
                logging.info(
                    "Transfer switch digital input service "
                    "NOT found"
                )
                self.transferSwitchActive = False
            elif not self.firstSearchDone:
                logging.warning(
                    "No transfer switch digital input found "
                    "with matching custom name"
                )
                self.firstSearchDone = True

    def verify_settings_change(self, expected_input_type, expected_current_limit, source_name):
        """Verify that AC input settings were applied correctly with retries"""
        max_verification_attempts = 5
        verification_interval = 1  # seconds
        
        # Small delay to allow D-Bus to propagate
        time.sleep(0.5)
        
        for attempt in range(max_verification_attempts):
            try:
                # Check input type
                actual_input_type = self.acInputTypeObj.GetValue()
                
                # Check current limit if adjustable
                current_limit_verified = True
                actual_current_limit = None
                
                if self.currentLimitIsAdjustableObj.GetValue() == 1:
                    actual_current_limit = self.currentLimitObj.GetValue()
                    # Allow small tolerance for floating point values
                    if abs(actual_current_limit - expected_current_limit) > 0.5:
                        current_limit_verified = False
                
                # Check if settings match
                if actual_input_type == expected_input_type and current_limit_verified:
                    logging.info(
                        f"✓ Verification successful for {source_name}: "
                        f"Input type={actual_input_type}, "
                        f"Current limit={actual_current_limit if actual_current_limit else 'N/A'}"
                    )
                    return True
                else:
                    logging.warning(
                        f"Verification attempt {attempt + 1}/{max_verification_attempts} "
                        f"for {source_name} failed: "
                        f"Expected input type {expected_input_type}, got {actual_input_type}; "
                        f"Expected current limit {expected_current_limit}, got {actual_current_limit}"
                    )
                    
                    # Attempt to fix if settings don't match
                    if actual_input_type != expected_input_type:
                        logging.info(f"Retrying input type change for {source_name}")
                        self.acInputTypeObj.SetValue(wrap_dbus_value(expected_input_type))
                    
                    if not current_limit_verified and self.currentLimitIsAdjustableObj.GetValue() == 1:
                        logging.info(f"Retrying current limit change for {source_name}")
                        self.currentLimitObj.SetValue(wrap_dbus_value(expected_current_limit))
                    
            except Exception as e:
                logging.error(f"Verification error for {source_name} (attempt {attempt + 1}): {e}")
            
            # Wait before retry
            if attempt < max_verification_attempts - 1:
                time.sleep(verification_interval)
        
        logging.error(f"✗ Failed to verify {source_name} settings after {max_verification_attempts} attempts")
        return False

    def settings_changed(self, setting, old_value, new_value):
        """Callback when settings are changed via D-Bus"""
        logging.info(f"Setting changed: {setting} = {new_value} (was {old_value})")
        
        # If generator current limit changed AND we're currently on generator,
        # push the new value to the active current limit
        if setting == 'generatorCurrentLimit' and self.dbusOk and self.transferSwitchActive:
            try:
                # Check if we're currently on generator
                current_input_type = self.acInputTypeObj.GetValue()
                if current_input_type == 2:  # On generator
                    if self.currentLimitIsAdjustableObj.GetValue() == 1:
                        logging.info(f"Generator current limit changed via D-Bus to {new_value}A - applying to active limit")
                        self.currentLimitObj.SetValue(wrap_dbus_value(new_value))
                        
                        # Verify the change
                        GLib.timeout_add_seconds(1, self.verify_generator_limit_change, new_value)
            except Exception as e:
                logging.error(f"Failed to apply changed generator limit: {e}")
        
        # If grid current limit changed AND we're currently on grid,
        # push the new value to the active current limit
        elif setting == 'gridCurrentLimit' and self.dbusOk and self.transferSwitchActive:
            try:
                # Check if we're currently on grid
                current_input_type = self.acInputTypeObj.GetValue()
                if current_input_type in (1, 3):  # On grid/shore
                    if self.currentLimitIsAdjustableObj.GetValue() == 1:
                        logging.info(f"Grid current limit changed via D-Bus to {new_value}A - applying to active limit")
                        self.currentLimitObj.SetValue(wrap_dbus_value(new_value))
                        
                        # Verify the change
                        GLib.timeout_add_seconds(1, self.verify_grid_limit_change, new_value)
            except Exception as e:
                logging.error(f"Failed to apply changed grid limit: {e}")

    def verify_generator_limit_change(self, expected_limit):
        """Verify generator current limit change was applied"""
        try:
            actual_limit = self.currentLimitObj.GetValue()
            if abs(actual_limit - expected_limit) > 0.5:
                logging.warning(f"Generator limit verification failed: expected {expected_limit}A, got {actual_limit}A - retrying")
                self.currentLimitObj.SetValue(wrap_dbus_value(expected_limit))
            else:
                logging.info(f"✓ Generator limit successfully updated to {actual_limit}A")
        except Exception as e:
            logging.error(f"Could not verify generator limit: {e}")
        return False

    def verify_grid_limit_change(self, expected_limit):
        """Verify grid current limit change was applied"""
        try:
            actual_limit = self.currentLimitObj.GetValue()
            if abs(actual_limit - expected_limit) > 0.5:
                logging.warning(f"Grid limit verification failed: expected {expected_limit}A, got {actual_limit}A - retrying")
                self.currentLimitObj.SetValue(wrap_dbus_value(expected_limit))
            else:
                logging.info(f"✓ Grid limit successfully updated to {actual_limit}A")
        except Exception as e:
            logging.error(f"Could not verify grid limit: {e}")
        return False

    def transferToGenerator(self):
        """Transfer to generator with atomic settings change"""
        if not self.dbusOk:
            return
        
        logging.info("switching to generator settings")
        
        # Apply generator settings atomically
        # Note: We no longer read/save current values - the saved values
        # are maintained dynamically by background monitoring
        try:
            # Change input type to generator (2)
            self.acInputTypeObj.SetValue(wrap_dbus_value(2))
            
            # Change current limit to stored generator value
            if self.currentLimitIsAdjustableObj.GetValue() == 1:
                logging.info(f"Applying generator current limit: {self.DbusSettings['generatorCurrentLimit']}A")
                self.currentLimitObj.SetValue(
                    wrap_dbus_value(self.DbusSettings['generatorCurrentLimit'])
                )
            
            # Verify both changes were applied
            self.verify_settings_change(
                expected_input_type=2,
                expected_current_limit=self.DbusSettings['generatorCurrentLimit'],
                source_name="generator"
            )
        except Exception as e:
            logging.error(f"Failed to apply generator settings: {e}")

    def apply_grid_settings(self):
        """Apply grid/shore settings to the inverter with atomic changes"""
        logging.info("switching to grid settings")
        
        # Apply grid settings atomically
        # Note: We no longer read/save current values - the saved values
        # are maintained dynamically by background monitoring
        try:
            # Queue AC input type change to grid/shore
            logging.info(f"Applying grid input type: {self.DbusSettings['gridInputType']}")
            self.acInputTypeObj.SetValue(self.DbusSettings['gridInputType'])
            
            # Queue current limit change if adjustable
            if self.currentLimitIsAdjustableObj.GetValue() == 1:
                logging.info(f"Applying grid current limit: {self.DbusSettings['gridCurrentLimit']}A")
                self.currentLimitObj.SetValue(
                    wrap_dbus_value(self.DbusSettings['gridCurrentLimit'])
                )
            
            # Verify both changes were applied
            self.verify_settings_change(
                expected_input_type=self.DbusSettings['gridInputType'],
                expected_current_limit=self.DbusSettings['gridCurrentLimit'],
                source_name="grid"
            )
        except Exception as e:
            logging.error(f"Failed to apply grid settings: {e}")

    def delayed_transfer_to_grid(self):
        """Handle delayed transfer to grid using GLib timeout"""
        logging.info("generator stopped - switching to grid/shore")
        
        def check_ignore_state():
            ignoreState = 0
            try:
                ignoreState = self.ignoreAcIn1Obj.GetValue()
                logging.info("IgnoreAcIn1 current state: %s", ignoreState)
            except Exception as e:
                logging.error("could not read /Ac/Control/IgnoreAcIn1: %s", e)

            if ignoreState == 1:
                logging.info("IgnoreAcIn1 is enabled - checking again in 5 seconds")
                GLib.timeout_add_seconds(5, self.disable_ignore_ac_in1)
            else:
                self.apply_grid_settings()
            return False  # Don't repeat this timeout

        GLib.timeout_add_seconds(5, check_ignore_state)

    def disable_ignore_ac_in1(self):
        """Disable IgnoreAcIn1 setting"""
        logging.info("Attempting to disable IgnoreAcIn1")
        try:
            self.ignoreAcIn1Obj.SetValue(wrap_dbus_value(0))
            GLib.timeout_add_seconds(1, self.verify_ignore_ac_in1_disabled)
        except Exception as e:
            logging.error("could not disable /Ac/Control/IgnoreAcIn1: %s", e)
            self.apply_grid_settings()  # Proceed anyway
        return False

    def verify_ignore_ac_in1_disabled(self):
        """Verify IgnoreAcIn1 was disabled successfully"""
        try:
            verifyState = self.ignoreAcIn1Obj.GetValue()
            if verifyState == 0:
                logging.info("Successfully disabled IgnoreAcIn1")
            else:
                logging.error("Failed to disable IgnoreAcIn1 - value still %s", verifyState)
        except Exception as e:
            logging.error("could not verify IgnoreAcIn1 disable: %s", e)
        self.apply_grid_settings()
        return False

    def transferToGrid(self):
        """Initiate transfer to grid with non-blocking delays"""
        if self.dbusOk:
            self.delayed_transfer_to_grid()

    def validate_settings(self):
        """Validate configuration settings before use"""
        valid = True
        
        # Validate current limits (typical max 100A, min 0A)
        try:
            grid_limit = self.DbusSettings['gridCurrentLimit']
            if grid_limit < 0 or grid_limit > 100:
                logging.error("Grid current limit out of range (0-100A): %s", grid_limit)
                valid = False
        except KeyError as e:
            logging.error("Missing gridCurrentLimit setting: %s", e)
            valid = False
            
        try:
            gen_limit = self.DbusSettings['generatorCurrentLimit']
            if gen_limit < 0 or gen_limit > 100:
                logging.error("Generator current limit out of range (0-100A): %s", gen_limit)
                valid = False
        except KeyError as e:
            logging.error("Missing generatorCurrentLimit setting: %s", e)
            valid = False
            
        # Validate input type (0=none, 1=grid, 2=generator, 3=shore)
        try:
            grid_type = self.DbusSettings['gridInputType']
            if grid_type not in (0, 1, 2, 3):
                logging.error("Grid input type invalid (0 - 3): %s", grid_type)
                valid = False
        except KeyError as e:
            logging.error("Missing gridInputType setting: %s", e)
            valid = False
            
        # Validate transfer switch setting (0 or 1)
        try:
            ts_ac2 = self.DbusSettings['transferSwitchOnAc2']
            if ts_ac2 not in (0, 1):
                logging.error("TransferSwitchOnAc2 invalid (0 or 1): %s", ts_ac2)
                valid = False
        except KeyError as e:
            logging.error("Missing transferSwitchOnAc2 setting: %s", e)
            valid = False
            
        return valid

    def delayed_startup(self):
        """Wait for D-Bus to stabilize before first search"""
        self.startup_delay_complete = True
        logging.info("Startup delay complete - transfer switch monitoring active")
        return False  # Don't repeat

    def background(self):
        """Main background loop with startup delay and dynamic value tracking"""
        # Skip processing until startup delay is complete
        if not self.startup_delay_complete:
            return True
        
        self.updateTransferSwitchState()
        self.getVeBusObjects()

        # Validate settings periodically
        if not hasattr(self, 'last_validation') or time.time() - self.last_validation > 300:  # Every 5 minutes
            if not self.validate_settings():
                logging.warning("Settings validation failed - check configuration")
            self.last_validation = time.time()

        # Dynamically track and update saved values based on current source
        # This handles the active -> saved direction
        if self.dbusOk and self.transferSwitchActive and self.currentLimitIsAdjustableObj:
            try:
                current_input_type = self.acInputTypeObj.GetValue()
                current_limit = self.currentLimitObj.GetValue()
                
                # If we're on generator (input type 2), update stored generator limit
                if current_input_type == 2:
                    if abs(current_limit - self.DbusSettings['generatorCurrentLimit']) > 0.5:
                        logging.info(f"Dynamic update: generator current limit from {self.DbusSettings['generatorCurrentLimit']}A to {current_limit}A")
                        self.DbusSettings['generatorCurrentLimit'] = current_limit
                
                # If we're on grid/shore (input type 1 or 3), update stored grid limit
                elif current_input_type in (1, 3):
                    if abs(current_limit - self.DbusSettings['gridCurrentLimit']) > 0.5:
                        logging.info(f"Dynamic update: grid current limit from {self.DbusSettings['gridCurrentLimit']}A to {current_limit}A")
                        self.DbusSettings['gridCurrentLimit'] = current_limit
                
                # Also track input type changes if they happen externally
                if hasattr(self, 'last_tracked_input_type') and current_input_type != self.last_tracked_input_type:
                    if current_input_type == 2:
                        logging.info(f"External input type change detected - now on generator (type {current_input_type})")
                        # Ensure generator limit is current
                        self.DbusSettings['generatorCurrentLimit'] = current_limit
                    elif current_input_type in (1, 3):
                        logging.info(f"External input type change detected - now on grid/shore (type {current_input_type})")
                        # Ensure grid limit is current
                        self.DbusSettings['gridCurrentLimit'] = current_limit
                    self.last_tracked_input_type = current_input_type
                    
            except Exception as e:
                logging.debug(f"Could not track dynamic values: {e}")

        # Skip processing if any dbus parameters were not initialized properly
        if self.dbusOk and self.transferSwitchActive:
            # Initial startup synchronization
            if self.lastOnGenerator is None:
                self.initial_startup_sync()
            # Normal runtime switching
            elif self.onGenerator != self.lastOnGenerator:
                if self.onGenerator:
                    self.transferToGenerator()
                else:
                    self.transferToGrid()
            self.lastOnGenerator = self.onGenerator
        elif self.onGenerator:
            self.transferToGrid()

        # Update main VE.Bus RemoteGeneratorSelected
        self.update_remote_generator_selected()

        return True

    def initial_startup_sync(self):
        """Synchronize AC input with transfer switch state on startup"""
        logging.info("initial startup sync - ensuring AC input matches current transfer switch state")
        
        # First, read current actual state to populate saved values
        try:
            current_input_type = self.acInputTypeObj.GetValue()
            current_limit = self.currentLimitObj.GetValue()
            
            if current_input_type == 2:
                # We're on generator, so update stored generator limit
                logging.info(f"Startup: On generator with {current_limit}A - updating stored generator limit")
                self.DbusSettings['generatorCurrentLimit'] = current_limit
            elif current_input_type in (1, 3):
                # We're on grid/shore, so update stored grid limit
                logging.info(f"Startup: On grid/shore with {current_limit}A - updating stored grid limit")
                self.DbusSettings['gridCurrentLimit'] = current_limit
                
            self.last_tracked_input_type = current_input_type
        except Exception as e:
            logging.error(f"Startup sync read failed: {e}")
        
        # Now sync with desired state from transfer switch
        if self.onGenerator:
            try:
                currentInputType = self.acInputTypeObj.GetValue()
                if currentInputType != 2:
                    logging.info("AC input not set for generator - correcting")
                    self.transferToGenerator()
                else:
                    logging.info("AC input already correctly set for generator")
            except Exception as e:
                logging.error("could not verify generator startup state: %s", e)
        else:
            try:
                currentInputType = self.acInputTypeObj.GetValue()
                if currentInputType != self.DbusSettings['gridInputType']:
                    logging.info("AC input not set for grid/shore - correcting")
                    self.transferToGrid()
                else:
                    logging.info("AC input already correctly set for grid/shore")
            except Exception as e:
                logging.error("could not verify grid startup state: %s", e)

    def update_remote_generator_selected(self):
        """Update RemoteGeneratorSelected D-Bus value"""
        if not self.dbusOk or not self.onGenerator:
            newRemoteGeneratorSelectedLocalValue = 0
        else:
            newRemoteGeneratorSelectedLocalValue = 1

        if self.remoteGeneratorSelectedItem is None:
            self.remoteGeneratorSelectedLocalValue = -1
        elif newRemoteGeneratorSelectedLocalValue != self.remoteGeneratorSelectedLocalValue:
            try:
                self.remoteGeneratorSelectedItem.SetValue(
                    wrap_dbus_value(newRemoteGeneratorSelectedLocalValue)
                )
            except:
                logging.error("could not set /Ac/Control/RemoteGeneratorSelected")
            self.remoteGeneratorSelectedLocalValue = newRemoteGeneratorSelectedLocalValue

    def __init__(self):
        self.theBus = dbus.SystemBus()

        self.onGenerator = False

        self.veBusService = ""
        self.lastVeBusService = ""

        self.acInputTypeObj = None
        self.numberOfAcInputs = 0

        self.currentLimitObj = None
        self.currentLimitIsAdjustableObj = None
        self.ignoreAcIn1Obj = None

        self.remoteGeneratorSelectedItem = None
        self.remoteGeneratorSelectedLocalValue = -1

        self.transferSwitchStateObj = None
        self.transferSwitchNameObj = None

        self.extTransferDigInputName = "transfer switch"

        self.lastOnGenerator = None
        self.transferSwitchActive = False
        self.dbusOk = False

        self.transferSwitchLocation = 0

        # Fixed race condition: increased initial delay to prevent premature D-Bus searches
        self.tsInputSearchDelay = 10  # Wait 10 seconds before first search
        self.startup_delay_complete = False  # Track startup state

        self.firstSearchDone = False
        self.veBusFoundInitially = False
        self.loggedVeBusInitialNotFound = False

        # Debouncing attributes
        self.debounce_timer = None
        self.pending_generator_state = None
        
        # Track external input type changes
        self.last_tracked_input_type = None

        settingsList = {
            'gridCurrentLimit': [
                '/Settings/TransferSwitch/GridCurrentLimit',
                0.0,
                0.0,
                0.0
            ],
            'generatorCurrentLimit': [
                '/Settings/TransferSwitch/GeneratorCurrentLimit',
                0.0,
                0.0,
                0.0
            ],
            'gridInputType': [
                '/Settings/TransferSwitch/GridType',
                0,
                0,
                0
            ],
            'stopWhenAcAvailable': [
                '/Settings/TransferSwitch/StopWhenAcAvailable',
                0,
                0,
                0
            ],
            'stopWhenAcAvailableFp': [
                '/Settings/TransferSwitch/StopWhenAcAvailableFp',
                0,
                0,
                0
            ],
            'transferSwitchOnAc2': [
                '/Settings/TransferSwitch/TransferSwitchOnAc2',
                0,
                0,
                0
            ],
        }

        self.DbusSettings = SettingsDevice(
            bus=self.theBus,
            supportedSettings=settingsList,
            timeout=10,
            eventCallback=self.settings_changed  # Add callback for external changes
        )

        # Validate initial settings
        if not self.validate_settings():
            logging.error("Initial settings validation failed - service may not operate correctly")

        if self.DbusSettings['gridInputType'] == 2:
            logging.warning(
                "grid input type was generator - resetting to grid"
            )
            self.DbusSettings['gridInputType'] = 1

        # Setup startup delay
        GLib.timeout_add_seconds(10, self.delayed_startup)
        
        # Run background task every second
        GLib.timeout_add_seconds(1, self.background)
        
        self.last_validation = time.time()

        return None


def main():
    from dbus.mainloop.glib import DBusGMainLoop

    DBusGMainLoop(set_as_default=True)

    logging.info(
        ">>>>>>>>>>>>>>>> "
        "Transfer Switch Monitor starting "
        "<<<<<<<<<<<<<<<<"
    )

    Monitor()

    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()
