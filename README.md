# mystat

A USB-controlled potentiostat/galvanostat for electrochemistry measurements.

## Introduction
This repository contains all the necessary design files to build a USB-controlled potentiostat/galvanostat.  This repository is a modification of the original [tdstatv3](https://github.com/thomasdob/tdstatv3) designed by Thomas Dobbelaere and described in detail at https://arxiv.org/pdf/1701.07650.pdf.  The purpose of this fork is to make design changes that facilitate placing the instrument inside a protected enclosure suitable for an electrochemical research environment, including a wet chemical lab bench, fume hood, or glove box.

This version of the hardware design uses a PCB with dimensions of 55.5 mm by 70 mm to fit an EMI-shielded [extruded aluminum case](https://www.hammfg.com/files/parts/pdf/1457C801E.pdf) (IP 54 rated).  This board will also fit a similar watertight [extruded aluminum case](https://www.hammfg.com/files/parts/pdf/1457C801.pdf) (IP 65 rated).  The terminal block and surface mounted USB mini-B connector in the original tdstav3 design by Thomas Dobbelaere were replaced with IP 67 rated circular connectors for the USB and electrode cables.

In order to fit the case, tall components (such as the reed relays and crystal) had to be moved from the board edges that slide into the slots in the extruded aluminum.  As a result, the entire PCB layout was changed.  All traces were made as wide as possible given the PCB size constraints and component footprints.  In addition, this forked design eliminates the power and mode LEDs present in the original tdstatv3.  The choice to eliminate LEDs was made to avoid the need for creating holes in the case that would accommodate them.

## USB access on Linux
In order to access the device without requiring root privileges, create a file
`/etc/udev/rules.d/99-tdstatv3.rules` containing the line

```
SUBSYSTEM=="usb", ATTRS{idVendor}=="a0a0", ATTRS{idProduct}=="0002", GROUP="plugdev", MODE="0666"
```

If you are using an Arduino or similar to control pumps for flow battery experiments, you will need to make a similar file for that device, for example
`/etc/udev/rules.d/99-arduino.rules` and
```
SUBSYSTEM=="usb", ATTR{idVendor}=="1a86", ATTR{idProduct}=="7523", GROUP="plugdev", MODE="0666"
```

This assumes that the current user is a member of the `plugdev` group, and that the default USB Vendor and Product ID's
as coded in the microcontroller firmware are used; if not, these values need to be adjusted.

Reload of rules may be needed:
```
sudo udevadm control --reload
sudo udevadm trigger
```

## Windows Driver

To install the USB drivers in Windows, you must have Windows version 7 or later.  Download and run the program Zadig.exe found [here](https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe).  The potentiostat should appear in the device list as "USB potentiostat/galvanostat".  Install the driver named "libusbK".

More information on Zadig can be found from its authors at: https://zadig.akeo.ie/

Zadig is based on libwdi and is licensed under GPLv3. The Zadig source code is found in the examples directory of the libwdi archive. The latest version of the source code may be downloaded from the git repository: https://github.com/pbatard/libwdi

## Credits
tdstatv3 created by:

* Thomas Dobbelaere
* email: thomas.dobbelaere@gmail.com

Previous fork created by:

* Matthew Yates
* email: myates@che.rochester.edu

Current fork created by:

* [Daniel Fernandez Pinto](https://chemisting.com) and [Kirk Smith](https://dualpower.supply)

## License
The contents of this repository are licensed under the GNU General Public License (GPL).
