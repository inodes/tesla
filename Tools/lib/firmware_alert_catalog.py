"""firmware_alert_catalog.py - what each Tesla firmware alert means, in plain English (REQ-202).

Tessie lists alerts by their firmware name (CP_a066_proxLatchedIdlePilot). This maps each name to a short title, a category,
a severity and a sentence on what happened and what (if anything) to do. Written from the descriptions Tessie / Tesla publish
with each alert. Names not listed here still get a readable title and category (decoded from the name) and use Tessie's own
description, so a new alert never shows up as raw code only.

Severity:  safety     a safety system acted or a door / trunk was open while driving
           attention  a feature or part is unavailable or faulty - worth checking if it keeps coming back
           notice     minor, or the car telling you something - no action needed
           info       routine; the car refusing a button press or confirming the obvious
Categories: Charging, Driving assist, Driving controls, Doors and closures, Dashcam and USB, Climate and body, Keys and security
"""
import re

SEVERITY_ORDER = {"safety": 0, "attention": 1, "notice": 2, "info": 3}

# name: (category, severity, title, what happened, what to do / what it means)
C = {
    # ---- Autopilot / FSD
    "APP_w009_aebFault": ("Driving assist", "attention", "Automatic emergency braking unavailable", "The car could not use automatic emergency braking because it lost contact with the drive unit.", "Usually clears on the next drive. If it keeps returning, book a service check."),
    "APP_w050_aeb_event": ("Driving assist", "safety", "Emergency braking applied", "The car saw a likely front collision and braked on its own.", "Nothing to do, but it marks a close call worth reviewing on the dashcam."),
    "APP_w073_issueEscalatedLDW": ("Driving assist", "safety", "Lane departure warning", "The car was drifting out of its lane and gave a visual and audible warning to take control.", "Check the dashcam to see what led up to it."),
    "APP_w081_pmmActiveBraking": ("Driving assist", "safety", "Pedal-mix-up braking", "The car braked because it thought the accelerator was pressed by mistake (pedal misapplication).", "Informational; review the footage if it was unexpected."),
    "APP_w207_autosteerUnavailable": ("Driving assist", "notice", "Autosteer unavailable when requested", "You tried to turn on Autosteer / FSD but it could not start (for example no lane lines, bad weather or a blocked camera).", "Normal on roads Autosteer cannot handle."),
    "APP_w216_driverOverriding": ("Driving assist", "info", "Your foot on the accelerator overrode cruise", "You pressed the accelerator for 10 seconds or more while cruise control was on, so the car did not brake.", "Nothing to do. Cruise will not brake until you lift off."),
    "APP_w264_lssFault": ("Driving assist", "attention", "Lane departure avoidance unavailable", "The lane support system had a fault, so lane departure warnings were off.", "Usually returns on the next drive; if it repeats, mention it at service."),
    "APP_w269_autopilotLimited": ("Driving assist", "attention", "Autopilot features limited", "The car found a condition that limits what Autopilot / FSD can do.", "Often clears on the next drive."),
    "APP_w304_lssCamBlockedStatus": ("Driving assist", "attention", "A camera was blocked or blinded", "A camera could not see properly (dirt, rain, glare or sun), which limits lane and side-collision features.", "Clean the cameras, or wait for visibility to return."),
    "APP_w390_cabinCamVisDegraded": ("Driving assist", "notice", "Cabin camera view unclear", "The camera watching the driver could not see clearly, so driver monitoring may be limited.", "Check nothing is covering the cabin camera (sunglasses, hat, sticker)."),
    "APS_w172_apFeaturesUnavailable": ("Driving assist", "attention", "Autopilot features temporarily unavailable", "Autopilot features were switched off by the car.", "Often returns on the next drive."),
    "APSB_w172_apFeaturesUnavailable": ("Driving assist", "attention", "Autopilot features temporarily unavailable", "Autopilot features were switched off by the car.", "Often returns on the next drive."),
    # ---- charging
    "CP_a053_proxLatchedNoPilot": ("Charging", "attention", "Charger not powered", "The cable is plugged in but the charger is not sending a signal, which usually means it has no power.", "Check the power source or try another charger."),
    "CP_a054_cableNotSecured": ("Charging", "attention", "Charge cable not latched", "The charge port did not lock the cable in place, so AC charging may be slower and fast charging may not work.", "Push the plug fully in and check for anything in the way."),
    "CP_a066_proxLatchedIdlePilot": ("Charging", "notice", "Charger connected but not ready", "The cable is plugged in but the charger has not started yet. It is very common and often just a charger waiting for you to start, schedule or authorise a charge.", "Start charging at the charger (or check its schedule). Not a car fault."),
    "CP_a074_failedToEstablishV2gComm": ("Charging", "attention", "Could not talk to the fast charger", "The car and a CCS fast charger could not agree to start communicating.", "Unplug, replug and retry, or try another stall."),
    "CP_a132_contractAuthTimeout": ("Charging", "notice", "Charger waiting for you to authorise", "The charger is still waiting for an app, card or plug-and-charge approval.", "Follow the charger's instructions to authorise."),
    "PCS2_a115_chgWallPowerRemoval": ("Charging", "attention", "Mains power dropped during AC charging", "The onboard charger saw wall power disappear while charging.", "Check for a tripped breaker, a wall charger fault or an unplugged cable."),
    "UMC_a006_contactorWelded": ("Charging", "attention", "Mobile Connector fault", "The Mobile Connector (portable charger) found its internal switch stuck. It will not charge in that state.", "Unplug from the wall, wait, retry. If it repeats, the connector needs service."),
    "UMC_a013_SA_Temperature": ("Charging", "attention", "Wall adapter problem: charging limited to 8 A", "The Mobile Connector could not read the temperature of its wall plug adapter.", "Push the adapter fully into the connector and retry."),
    "UMC_a014_SA_Genealogy": ("Charging", "attention", "Wall adapter problem: charging limited to 8 A", "The Mobile Connector could not read the identity data of its wall plug adapter.", "Push the adapter fully into the connector and retry."),
    "VCFRONT_a186_noDriveChgCableCon": ("Charging", "info", "Tried to drive with the charge cable plugged in", "You selected Drive while a charge cable was connected. The car will not move until it is unplugged.", "Unplug the cable."),
    "VCFRONT2_a186_noDriveChgCableCon": ("Charging", "info", "Tried to drive with the charge cable plugged in", "You selected Drive while a charge cable was connected. The car will not move until it is unplugged.", "Unplug the cable."),
    # ---- driving controls
    "DI_a133_vehicleHoldTimedOut": ("Driving controls", "notice", "Car put itself in Park after a long hold", "The car had been held stationary for a long time (Vehicle Hold) so it shifted to Park.", "Shift into D or R to drive off."),
    "DI_a163_shiftMotorSpeed": ("Driving controls", "info", "Gear change refused: moving too fast", "A change between Drive and Reverse was refused because the car was still moving.", "Stop first."),
    "DI_a164_brakeOverride": ("Driving controls", "info", "Both pedals pressed", "The brake and accelerator were pressed together, so the car cut motor power.", "Normal if you touched both. Release one."),
    "DI_a172_accelPressedInNP": ("Driving controls", "info", "Accelerator pressed in Park or Neutral", "The accelerator was pressed while the car was in Park or Neutral, so nothing happened.", "Shift into D or R."),
    "DI_a175_crsNotAvailable": ("Driving controls", "notice", "Cruise control unavailable", "The car could not turn on cruise control.", "Usually temporary."),
    "DI_a180_crsSeatbeltUnbuckled": ("Driving controls", "notice", "Cruise control off: seat belt unbuckled", "Cruise control is unavailable while the driver's seat belt is unbuckled.", "Buckle up."),
    "DI_a183_holdReleaseRqrd": ("Driving controls", "info", "Vehicle Hold is on", "The car reminded you that Vehicle Hold is holding it still.", "Press the brake or accelerator to release."),
    "DI_a189_garageShiftWithPedal": ("Driving controls", "info", "Gear change refused: accelerator pressed", "A gear change was refused because the accelerator was pressed.", "Lift off the accelerator and try again."),
    "DI_a191_brakeShiftReq": ("Driving controls", "info", "Gear change needs the brake", "A gear change was refused because the brake was not pressed.", "Press the brake first."),
    "DI_a246_opdUnavailable": ("Driving controls", "attention", "Automatic Vehicle Hold unavailable", "The car could not hold itself still on slopes in Hold stopping mode.", "Use the brake pedal when stopping."),
    "GTW_w396_alreadyInReverse": ("Driving controls", "info", "Already in Reverse", "A request to select Reverse arrived but the car was already in Reverse.", "Informational only."),
    "GTW_w397_alreadyInDrive": ("Driving controls", "info", "Already in Drive", "A request to select Drive arrived but the car was already in Drive.", "Informational only."),
    # ---- doors / closures
    "UI_a001_DriverDoorOpen": ("Doors and closures", "safety", "Driver's door open while in gear", "The driver's door was open when the car was in Drive or Reverse.", "Close it before moving."),
    "UI_a002_DoorOpen": ("Doors and closures", "safety", "Door open while in gear", "A passenger or rear door was open when the car was in Drive or Reverse.", "Close it before moving."),
    "UI_a004_FrunkOpen": ("Doors and closures", "safety", "Front trunk open while in gear", "The front trunk was not fully latched when the car was in gear.", "Stop and close it."),
    "VCLEFT_a182_emergencyLatchRel": ("Doors and closures", "notice", "Manual door release used", "The mechanical door release was used to open a door, which can scuff the window trim.", "Use the normal door button when you can."),
    "VCRIGHT_a551_interiorDoorRequestInhibited": ("Doors and closures", "notice", "Door opening blocked: someone passing", "The car held the door shut because a pedestrian, cyclist or vehicle was passing. Pressing again opens it.", "Look before opening."),
    # ---- dashcam / USB / display
    "UI_a079_DashcamUsbDriveFull": ("Dashcam and USB", "attention", "Dashcam drive is full", "The USB drive used for the dashcam has no space left, so Dashcam and Sentry could not record.", "Free up space or swap the drive."),
    "UI_a080_DashcamUsbDriveSlow": ("Dashcam and USB", "attention", "Dashcam drive too slow", "The USB drive could not write fast enough, so clips may be lost.", "Use a faster drive."),
    "UI_a108_DashcamUsbDriveGettingFull": ("Dashcam and USB", "attention", "Dashcam drive nearly full", "The USB drive is running out of room for the last 60 minutes of Recent Clips.", "Delete files or reformat the drive."),
    "UI_a112_badUSBDevice": ("Dashcam and USB", "attention", "A USB device misbehaved", "A plugged-in USB device caused errors and the car computer may have restarted.", "Unplug the device and try another."),
    "UI_a114_drivingVisualizationDegraded": ("Dashcam and USB", "attention", "Screen visualisation degraded", "Lane lines and other vehicles may not have been drawn on the touchscreen.", "Usually returns on the next drive."),
    "UI_a266_DashcamRecentBufferDurationReduced": ("Dashcam and USB", "notice", "Dashcam keeps less recent footage", "The car shortened how many minutes of Recent Clips it keeps because of drive space or speed.", "Check the dashcam drive."),
    # ---- climate / body
    "VCFRONT_a004_adaptiveHeadlightsUnavailable": ("Climate and body", "attention", "Adaptive headlights unavailable", "An input to the adaptive headlights failed, so they were off.", "Use the high-beam button manually."),
    "VCFRONT_a185_washerFluidLow": ("Climate and body", "notice", "Washer fluid low", "The windscreen washer fluid is low.", "Top it up."),
    "VCFRONT_a361_washerFluidLowMomentary": ("Climate and body", "notice", "Washer fluid low", "The windscreen washer fluid read low for a moment (it can slosh on slopes).", "Top it up if it keeps returning."),
    "VCFRONT_a447_cabinHVACUnavailable": ("Climate and body", "attention", "Cabin heating / cooling limited", "A fault in the thermal system reduced or removed cabin heating or cooling.", "If it persists, book a service check."),
    "VCFRONT2_a447_cabinHVACUnavailable": ("Climate and body", "attention", "Cabin heating / cooling limited", "A fault in the thermal system reduced or removed cabin heating or cooling.", "If it persists, book a service check."),
    "VCSEAT2R_a190_seatChoreographyIsBlockedByFrontSeat": ("Climate and body", "info", "Rear seat movement blocked by front seat", "A rear seat could not move automatically because the front seat was in the way.", "Move the front seat forward."),
    # ---- keys
    "VCSEC_a260_prsntPhoneKeyDisconnected": ("Keys and security", "notice", "Phone key disconnected", "The phone key lost its Bluetooth link, so Walk-Away Lock may not work.", "Lock the car manually, or reconnect the phone."),
}

PREFIX = {"APP": ("Driving assist", "Autopilot"), "APS": ("Driving assist", "Autopilot"), "APSB": ("Driving assist", "Autopilot"), "CP": ("Charging", "charge port"),
          "PCS": ("Charging", "onboard charger"), "PCS2": ("Charging", "onboard charger"), "UMC": ("Charging", "Mobile Connector"), "DI": ("Driving controls", "drive unit"),
          "GTW": ("Driving controls", "gateway"), "UI": ("Dashcam and USB", "touchscreen"), "VCFRONT": ("Climate and body", "front body controller"),
          "VCFRONT2": ("Climate and body", "front body controller"), "VCLEFT": ("Doors and closures", "left body controller"), "VCRIGHT": ("Doors and closures", "right body controller"),
          "VCSEC": ("Keys and security", "security controller"), "BMS": ("Charging", "battery management"), "THC": ("Climate and body", "thermal controller")}


def words(s):
    """proxLatchedIdlePilot -> 'prox latched idle pilot'"""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s).replace("_", " ").lower()


def explain(name, row=None):
    """dict(category, severity, title, what, advice, known) for an alert name. row = a Tessie row for the fallback text."""
    if name in C:
        c, s, t, w, a = C[name]
        return dict(category=c, severity=s, title=t, what=w, advice=a, known=True)
    m = re.match(r"^([A-Za-z0-9]+?)_([aw])(\d+)_(.*)$", name)
    pre, kind, _, rest = (m.groups() if m else ("", "a", "", name))
    cat, part = PREFIX.get(pre, ("Other", pre or "car"))
    title = words(rest).capitalize() if rest else name
    what = ((row or {}).get("Description") or "").strip() or "The %s reported \"%s\"." % (part, words(rest))
    advice = ((row or {}).get("Impact") or "").strip()
    return dict(category=cat, severity="notice" if kind == "w" else "attention", title=title, what=what, advice=advice, known=False)
