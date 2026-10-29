# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Upcoming [1.X.0]

### Added

- Connection point to GND of instrument circuitry (as in 1st paper)
- Recommended housing to schematic

### Fixed

- Buffer U11 changed to thermal pad variant and added thermal pours to avoid overheating this component and extend steady-state high-current operation

### Changed

- Micro USB connector is now USB-C (2.0) and no longer splashproof
- Cell connector is no longer audio jack type, revert to screw connections
- Reed relays are back to regular size and previous model from 1st paper
- C2, C10-C15, C17, C18, C23, C25-C32, C34-C37 changed from AVX P/N 08055C104KAT2A to YAGEO P/N CC0805KRX7R9BB104
- C5, C6, C8, C9, C39 changed from TDK P/N C2012X7R1V475K125AC to Samsung P/N CL21A475KAQNNNE
- C19, C20, C33, C38 changed from YAGEO P/N CC0805JRNPO9BN120 to Samsung P/N CL10C120JB8NNNC
- FB1 changed from Vishay Dale P/N ILBB0805ER320V to APV P/N APBH2012-330WTD16
- J1 changed from CUI to HCTL P/N HC-DC-005-2.0M
- Many resistor manufacturer changes, but not values/precisions


### Removed