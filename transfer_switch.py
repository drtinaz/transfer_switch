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

    def getVeBusObjects(self):

        vebusService = ""

        # invalidate all local parameters if transfer switch is not active
        if not self.transferSwitchActive:

            try:
                if self.remoteGeneratorSelectedItem != None:
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

            return

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

            elif (
                not self.veBusFoundInitially and
                not self.loggedVeBusInitialNotFound
            ):

                logging.warning(
                    "Multi/Quattro (VE.Bus) service not found "
                    "on startup: %s",
                    e
                )

                self.loggedVeBusInitialNotFound = True

            self.veBusService = ""
            self.dbusOk = False
            self.numberOfAcInputs = 0
            self.acInputTypeObj = None

        except Exception as e:

            logging.error(
                "Unexpected error while looking for VE.Bus service: %s",
                e
            )

            self.veBusService = ""
            self.dbusOk = False
            self.numberOfAcInputs = 0
            self.acInputTypeObj = None

        if vebusService == "---":

            if self.veBusService != "":
                logging.info("Multi/Quattro disappeared")

            self.veBusService = ""
            self.dbusOk = False
            self.numberOfAcInputs = 0

        elif self.veBusService == "" or vebusService != self.veBusService:

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

            try:

                self.remoteGeneratorSelectedItem = self.theBus.get_object(
                    vebusService,
                    "/Ac/Control/RemoteGeneratorSelected"
                )

            except Exception as e:

                logging.error(
                    "Failed to get "
                    "/Ac/Control/RemoteGeneratorSelected: %s",
                    e
                )

                self.remoteGeneratorSelectedItem = None
                self.remoteGeneratorSelectedLocalValue = -1

            if self.numberOfAcInputs == 0:

                self.dbusOk = False
                self.veBusFoundInitially = False

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

        # determine transfer switch location
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

            except Exception as e:

                self.dbusOk = False

                logging.error(
                    "AC input dbus setup failed: %s",
                    e
                )

    def updateTransferSwitchState(self):

        inputValid = False

        if self.transferSwitchActive and self.transferSwitchNameObj:

            try:

                name = self.transferSwitchNameObj.GetValue()

                if self.extTransferDigInputName.lower() in name.lower():

                    state = self.transferSwitchStateObj.GetValue()

                    if state in (12, 3):

                        inputValid = True
                        self.onGenerator = True

                    elif state in (13, 2):

                        inputValid = True
                        self.onGenerator = False

                else:

                    logging.info(
                        "Current transfer switch input name '%s' "
                        "does not match '%s'",
                        name,
                        self.extTransferDigInputName
                    )

            except Exception as e:

                logging.error(
                    "Error accessing transfer switch D-Bus object: %s",
                    e
                )

                inputValid = False

        if not inputValid and self.transferSwitchActive:

            logging.info(
                "Transfer switch digital input no longer valid "
                "or name mismatch"
            )

            self.transferSwitchActive = False
            self.transferSwitchNameObj = None

        elif not inputValid and self.tsInputSearchDelay >= 10:

            newInputService = ""
            custom_name = ""
            found = False

            for service in self.theBus.list_names():

                if service.startswith(
                    "com.victronenergy.digitalinput"
                ):

                    try:

                        name_obj = self.theBus.get_object(
                            service,
                            '/CustomName'
                        )

                        custom_name_val = name_obj.GetValue()

                        if (
                            self.extTransferDigInputName.lower()
                            in custom_name_val.lower()
                        ):

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

        if self.transferSwitchActive:

            self.tsInputSearchDelay = 0

        else:

            self.onGenerator = False

            if self.tsInputSearchDelay < 10:
                self.tsInputSearchDelay += 1
            else:
                self.tsInputSearchDelay = 0

    def transferToGrid(self):

        if self.dbusOk:

            logging.info(
                "generator stopped - waiting 5 seconds before "
                "switching to grid/shore"
            )

            time.sleep(5)

            ignoreState = 0

            try:

                ignoreState = self.ignoreAcIn1Obj.GetValue()

                logging.info(
                    "IgnoreAcIn1 current state: %s",
                    ignoreState
                )

            except Exception as e:

                logging.error(
                    "could not read /Ac/Control/IgnoreAcIn1: %s",
                    e
                )

            if ignoreState == 1:

                logging.info(
                    "IgnoreAcIn1 is enabled - waiting another "
                    "5 seconds"
                )

                time.sleep(5)

                try:

                    ignoreState = self.ignoreAcIn1Obj.GetValue()

                    logging.info(
                        "IgnoreAcIn1 second check state: %s",
                        ignoreState
                    )

                except Exception as e:

                    logging.error(
                        "could not re-read "
                        "/Ac/Control/IgnoreAcIn1: %s",
                        e
                    )

                    ignoreState = 0

            if ignoreState == 1:

                logging.info(
                    "IgnoreAcIn1 still enabled - attempting "
                    "to disable"
                )

                try:

                    self.ignoreAcIn1Obj.SetValue(
                        wrap_dbus_value(0)
                    )

                    time.sleep(1)

                    verifyState = self.ignoreAcIn1Obj.GetValue()

                    if verifyState == 0:

                        logging.info(
                            "Successfully disabled IgnoreAcIn1"
                        )

                    else:

                        logging.error(
                            "Failed to disable IgnoreAcIn1 - "
                            "value still %s",
                            verifyState
                        )

                except Exception as e:

                    logging.error(
                        "could not disable "
                        "/Ac/Control/IgnoreAcIn1: %s",
                        e
                    )

            logging.info("switching to grid settings")

            try:

                self.DbusSettings[
                    'generatorCurrentLimit'
                ] = self.currentLimitObj.GetValue()

            except:

                logging.error(
                    "dbus error generator AC input current "
                    "limit not saved switching to grid"
                )

            try:

                self.acInputTypeObj.SetValue(
                    self.DbusSettings['gridInputType']
                )

            except:

                logging.error(
                    "dbus error AC input type not changed to grid"
                )

            try:

                if self.currentLimitIsAdjustableObj.GetValue() == 1:

                    self.currentLimitObj.SetValue(
                        wrap_dbus_value(
                            self.DbusSettings['gridCurrentLimit']
                        )
                    )

                else:

                    logging.warning(
                        "Input current limit not adjustable"
                    )

            except:

                logging.error(
                    "dbus error AC input current limit not "
                    "changed switching to grid"
                )

    def transferToGenerator(self):

        if self.dbusOk:

            logging.info("switching to generator settings")

            try:

                inputType = self.acInputTypeObj.GetValue()

                if inputType == 2:

                    logging.warning(
                        "grid input cannot be generator - "
                        "setting to grid"
                    )

                    inputType = 1

                self.DbusSettings['gridInputType'] = inputType

            except:

                logging.error(
                    "dbus error AC input type not saved "
                    "when switching to generator"
                )

            try:

                self.DbusSettings[
                    'gridCurrentLimit'
                ] = self.currentLimitObj.GetValue()

            except:

                logging.error(
                    "dbus error AC input current limit not "
                    "saved when switching to generator"
                )

            try:

                self.acInputTypeObj.SetValue(2)

            except:

                logging.error(
                    "dbus error AC input type not changed "
                    "when switching to generator"
                )

            try:

                if self.currentLimitIsAdjustableObj.GetValue() == 1:

                    self.currentLimitObj.SetValue(
                        wrap_dbus_value(
                            self.DbusSettings[
                                'generatorCurrentLimit'
                            ]
                        )
                    )

                else:

                    logging.warning(
                        "Input current limit not adjustable"
                    )

            except:

                logging.error(
                    "dbus error AC input current limit not "
                    "changed when switching to generator"
                )

    def background(self):

        self.updateTransferSwitchState()
        self.getVeBusObjects()

        # skip processing if any dbus parameters
        # were not initialized properly
        if self.dbusOk and self.transferSwitchActive:

            # initial startup synchronization
            if self.lastOnGenerator is None:

                logging.info(
                    "initial startup sync - ensuring AC input "
                    "matches current transfer switch state"
                )

                if self.onGenerator:

                    try:

                        currentInputType = (
                            self.acInputTypeObj.GetValue()
                        )

                        if currentInputType != 2:

                            logging.info(
                                "AC input not set for generator "
                                "- correcting"
                            )

                            self.transferToGenerator()

                        else:

                            logging.info(
                                "AC input already correctly set "
                                "for generator"
                            )

                    except Exception as e:

                        logging.error(
                            "could not verify generator "
                            "startup state: %s",
                            e
                        )

                else:

                    try:

                        currentInputType = (
                            self.acInputTypeObj.GetValue()
                        )

                        if (
                            currentInputType !=
                            self.DbusSettings['gridInputType']
                        ):

                            logging.info(
                                "AC input not set for grid/shore "
                                "- correcting"
                            )

                            self.transferToGrid()

                        else:

                            logging.info(
                                "AC input already correctly set "
                                "for grid/shore"
                            )

                    except Exception as e:

                        logging.error(
                            "could not verify grid startup state: %s",
                            e
                        )

            # normal runtime switching
            elif self.onGenerator != self.lastOnGenerator:

                if self.onGenerator:
                    self.transferToGenerator()
                else:
                    self.transferToGrid()

            self.lastOnGenerator = self.onGenerator

        elif self.onGenerator:

            self.transferToGrid()

        # update main VE.Bus RemoteGeneratorSelected
        if not self.dbusOk or not self.onGenerator:

            newRemoteGeneratorSelectedLocalValue = 0

        else:

            newRemoteGeneratorSelectedLocalValue = 1

        if self.remoteGeneratorSelectedItem == None:

            self.remoteGeneratorSelectedLocalValue = -1

        elif (
            newRemoteGeneratorSelectedLocalValue !=
            self.remoteGeneratorSelectedLocalValue
        ):

            try:

                self.remoteGeneratorSelectedItem.SetValue(
                    wrap_dbus_value(
                        newRemoteGeneratorSelectedLocalValue
                    )
                )

            except:

                logging.error(
                    "could not set "
                    "/Ac/Control/RemoteGeneratorSelected"
                )

            self.remoteGeneratorSelectedLocalValue = (
                newRemoteGeneratorSelectedLocalValue
            )

        return True

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

        self.tsInputSearchDelay = 99

        self.firstSearchDone = False
        self.veBusFoundInitially = False
        self.loggedVeBusInitialNotFound = False

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

            'stopWhenAcAvaiable': [
                '/Settings/TransferSwitch/StopWhenAcAvailable',
                0,
                0,
                0
            ],

            'stopWhenAcAvaiableFp': [
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
            eventCallback=None
        )

        if self.DbusSettings['gridInputType'] == 2:

            logging.warning(
                "grid input type was generator - resetting to grid"
            )

            self.DbusSettings['gridInputType'] = 1

        GLib.timeout_add(1000, self.background)

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


main()