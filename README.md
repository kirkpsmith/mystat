# mystatv1

A USB-controlled potentiostat/galvanostat for electrochemistry measurements.

## Introduction
This repository contains all the necessary design files to build your own USB-controlled potentiostat/galvanostat.  This repository is a modification of the original [tdstatv3](https://github.com/thomasdob/tdstatv3) designed by Thomas Dobbelaere and described in detail at https://arxiv.org/pdf/1701.07650.pdf.  The purpose of this fork is to make design changes that facilitate placing the instrument inside a protected enclosure suitable for an electrochemical research environment, including a wet chemical lab bench, fume hood, or glove box.

This version of the hardware design uses a PCB with dimensions matched to fit an [EMI/RFI-shielded extruded aluminum case](https://www.hammfg.com/electronics/small-case/extruded/1457-emi) (IP 54 rated).  The terminal block and surface mounted USB mini-B connector in the original design were replaced with IP 67 rated circular connectors for the USB and electrode cables.

NOTE/WARNING:  This is still a work in progress.  I've built and tested a few boards using the original tdstatv3 design and they work well.  However, the design changes shown here have not been implemented into physical hardware yet.  There may be unforseen problems with it.  I'm a Chemical Engineer with virtually zero experience in circuit design.  As such, I don't really know what I am doing, so you have been warned!

## Repository contents

### Directories
* `kicad`: KiCad design files (schematic diagram and PCB layout).
* `firmware`: Source code and compiled firmware for the PIC16F1459 microcontroller. Uses Microchip's XC8 compiler.
* `python`: Contains `tdstatv3.py`; run this file with Python 3 to bring up a GUI measurement tool.
* `gerber`: PCB design files in Gerber format, the universal standard for PCB manufacturing.
* `datasheets`: Datasheets in pdf format for the integrated circuits used in this design.
* `drivers`: Libusb drivers for Windows (not necessary on other operating systems).

### Files
* `full_schematic.pdf`: Complete schematic diagram in pdf format.
* `parts_list.txt`: Complete parts list, useful when ordering components.
* `pcb_3dview.png`: 3D renders of the PCB design, useful for verification of the PCB layout.
* `pcb_fabrication_diagram.pdf`: Front side of PCB with component locations and values, useful for circuit assembly.
* `photograph.jpg`: A photograph of the assembled circuit, using a PCB manufactured by [OSH Park](https://oshpark.com/).
* `LICENSE`: a copy of the GNU GPLv3 license.
* `README.md`: This file.

## USB access on Linux
In order to access the device without requiring root privileges, create a file
`/etc/udev/rules.d/99-tdstatv3.rules` containing the line

```
SUBSYSTEM=="usb", ATTRS{idVendor}=="a0a0", ATTRS{idProduct}=="0002", GROUP="plugdev", MODE="0666"
```
This assumes that the current user is a member of the `plugdev` group, and that the default USB Vendor and Product ID's
as coded in the microcontroller firmware are used; if not, these values need to be adjusted.

## Credits
tdstatv3 created by:

* Thomas Dobbelaere
* email: thomas.dobbelaere@gmail.com

this fork created by:

* Matthew Yates
* email: myates@che.rochester.edu

## License
The contents of this repository are licensed under the GNU General Public License (GPL).
