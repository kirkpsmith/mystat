#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# This Python program allows control over the USB potentiostat/galvanostat using a graphical user interface. It supports real-time data acquisition and plotting, manual control and
# calibration, and three pre-programmed measurement methods geared towards battery research (staircase cyclic voltammetry, constant-current charge/discharge, and rate testing).
# It is cross-platform, requiring only a working installation of Python 3.x together with the Numpy, Scipy, PyUSB, and PyQtGraph packages.


# Author: Thomas Dobbelaere, modified by Matthew Yates and Paul Irving / modified by Daniel Fernandez and Kirk Smith for the FBRC and use with flow batteries.
# License: GPL
from PyQt5 import QtWidgets, QtGui, QtCore
import pyqtgraph
import sys, platform
import time, datetime, timeit
import usb.core, usb.util
import os.path
import collections
import numpy as np
import scipy.integrate
import serial
import glob
from time import sleep
from version import __version__


#import pkg_resources.py2_warn

VERSION_NUMBER = __version__

# basedir = os.path.dirname(__file__)

usb_vid = "0xa0a0" # Default USB vendor ID, can also be adjusted in the GUI
usb_pid = "0x0002" # Default USB product ID, can also be adjusted in the GUI
selected_usb_identifier = None  # Currently selected device identifier from dropdown
current_range_list = ["200 mA", u"20 mA", u"200 µA", u"2 µA"]

arduino_vid = "0x1a86"  # Default Arduino VID
arduino_pid = "0x7523"  # Default Arduino PID
arduino_dev = None  # USB device handle for Arduino
arduino_out_endpoint = 1  # Bulk OUT endpoint
arduino_in_endpoint = 0x81  # Bulk IN endpoint
selected_arduino_identifier = None  # Currently selected device identifier (bus, port_numbers, serial)
shunt_calibration = [1.,1.,1.,1.] # Fine adjustment for shunt resistors, containing values of R1/1ohm, R2/10ohm, R3/1kohm, R4/100kohm (can also be adjusted in the GUI)
currentrange = 0 # Default current range (expressed as index in current_range_list)
units_list = ["Potential (V)", "Current (mA)", "DAC Code"]
dev = None # Global object which is reserved for the USB device
current_offset = 0. # Current offset in DAC counts
potential_offset = 0. # Potential offset in DAC counts
potential = 0. # Measured potential in V
current = 0. # Measured current in mA
last_potential_values = collections.deque(maxlen=200)
last_current_values = collections.deque(maxlen=200)
raw_potential = 0 # Measured potential in ADC counts
raw_current = 0 # Measured current in ADC counts
last_raw_potential_values = collections.deque(maxlen=200)
last_raw_current_values = collections.deque(maxlen=200)
cv_parameters = {} # Dictionary to hold the CV parameters
cd_parameters = {} # Dictionary to hold the charge/discharge parameters
rate_parameters = {} # Dictionary to hold the rate testing parameters
overcounter, undercounter, skipcounter = 0, 0, 0 # Global counters used for automatic current ranging
cd_allow_edit_during_cycling = False # Toggle to allow parameter editing during cycling
time_of_last_adcread = 0.
adcread_interval = 0.09 # ADC sampling interval (in seconds)
logging_enabled = False # Enable logging of potential and current in idle mode (can be adjusted in the GUI)
time_from_last_arduino_update = 0.0
cd_wait_start_time = 0
cd_waittime = 0
cd_stop_after_next_discharge = False
cd_waiting_after_discharge = False
cd_discharge_wait_start_time = 0
cd_discharge_waittime = 30

log_file_handle = None


if platform.system() != "Windows":
    # On Linux/OSX, use the Qt timer
    busyloop_interval = 0
    qt_timer_period = 1e3*adcread_interval # convert to ms
else:
    # On MS Windows, system timing is inaccurate, so use a busy loop instead
    busyloop_interval = adcread_interval
    qt_timer_period = 0

class AverageBuffer:
    """Collect samples and compute an average as soon as a sufficient number of samples is added."""
    def __init__(self, number_of_samples_to_average):
        self.number_of_samples_to_average = number_of_samples_to_average
        self.samples = []
        self.averagebuffer = []
    
    def add_sample(self, sample):
        self.samples.append(sample)
        if len(self.samples) >= self.number_of_samples_to_average:
            self.averagebuffer.append(sum(self.samples)/len(self.samples))
            self.samples = []

    def clear(self):
        self.samples = []
        self.averagebuffer = []

class States:
    """Expose a named list of states to be used as a simple state machine."""
    NotConnected, Idle_Init, Idle, Measuring_Offset, Stationary_Graph, Measuring_CV, Measuring_CD, Measuring_Rate = range(8)

state = States.NotConnected # Initial state

def serial_ports():
    """ Lists serial port names

        :raises EnvironmentError:
            On unsupported or unknown platforms
        :returns:
            A list of the serial ports available on the system
    """
    if sys.platform.startswith('win'):
        ports = ['COM%s' % (i + 1) for i in range(256)]
    elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
        # this excludes your current terminal "/dev/tty"
        ports = glob.glob('/dev/tty[A-Za-z]*')
    elif sys.platform.startswith('darwin'):
        ports = glob.glob('/dev/tty.*')
    else:
        raise EnvironmentError('Unsupported platform')

    result = []
    for port in ports:
        try:
            # Use very short timeout to avoid hanging
            s = serial.Serial(port, timeout=0.1)
            s.close()
            result.append(port)
        except (OSError, serial.SerialException):
            pass
    return result

def find_devices_by_vid_pid(target_vid, target_pid):
    """Find all devices matching the given VID and PID.
    
    Returns a list of ((bus, port_numbers, serial), label) tuples.
    Uses bus/port/serial to differentiate multiple devices.
    """
    devices = []
    target_vid_int = int(target_vid, 0)
    target_pid_int = int(target_pid, 0)
    
    try:
        all_devs = usb.core.find(idVendor=target_vid_int, idProduct=target_pid_int, find_all=True)
        
        for dev in all_devs:
            try:
                bus = dev.bus
                port_numbers = dev.port_numbers 
                port_str = '.'.join(map(str, port_numbers))
                
                try:
                    serial = usb.util.get_string(dev, dev.iSerialNumber)
                except:
                    serial = None
                
                identifier = (bus, port_numbers, serial)
                
                if serial:
                    label = f"{port_str} ({serial})"
                else:
                    label = port_str
                
                devices.append((identifier, label))
                usb.util.dispose_resources(dev)
            except Exception:
                pass
    except Exception:
        pass
    
    return devices

def device_matches(dev, bus, port_numbers, serial):
    """Check if a USB device matches the given identifiers."""
    if dev.bus != bus:
        return False
    if dev.port_numbers != port_numbers:
        return False
    if serial:
        try:
            return usb.util.get_string(dev, dev.iSerialNumber) == serial
        except:
            return False
    return True

def find_bulk_endpoints(dev):
    """Find bulk IN and OUT endpoints for a USB device.
    
    Returns (bulk_out, bulk_in) endpoint addresses.
    """
    #this line is important lol https://github.com/pyusb/pyusb/issues/391
    #also from here: https://stackoverflow.com/questions/23203563/pyusb-device-claimed-detach-kernel-driver-return-entity-not-found
    c = 1
    for config in dev:
        for i in range(config.bNumInterfaces):
            if dev.is_kernel_driver_active(i):
                dev.detach_kernel_driver(i)
        c+=1

    dev.set_configuration()

    cfg = dev.get_active_configuration()
    intf = cfg[(0,0)]


    ep = usb.util.find_descriptor(
        intf,
        # match the first OUT endpoint
        custom_match = \
        lambda e: \
            usb.util.endpoint_direction(e.bEndpointAddress) == \
            usb.util.ENDPOINT_OUT)
    bulk_out = ep
    ep = usb.util.find_descriptor(
    intf,
    # match the first OUT endpoint
    custom_match = \
    lambda e: \
        usb.util.endpoint_direction(e.bEndpointAddress) == \
        usb.util.ENDPOINT_IN)
    bulk_in = ep
    return (bulk_out, bulk_in)

def check_safety_limits():
    """Dedicated function to check safety limits more frequently during measurements"""
    global state
    
    if state != States.Measuring_CD:
        return
        
    try:
        # Check if we have the necessary parameters
        if 'cd_parameters' not in globals() or 'ulimit' not in cd_parameters:
            return
            
        # Check for safety threshold warning (95% of ulimit)
        warning_threshold = cd_parameters['ulimit'] * 0.995
        if potential > warning_threshold:
            log_message(f"WARNING: Approaching safety limit - {potential:.3f}V (limit: {cd_parameters['ulimit']:.3f}V)")
            
    except Exception as e:
        log_message(f"Error in safety check: {str(e)}")

def cd_update_parameters():
    """Update charge/discharge parameters during runtime"""
    global cd_parameters
    
    if state != States.Measuring_CD:
        log_message("Update Parameters: Parameters can only be updated during a running measurement.")
        return
    
    # Get current values from GUI
    try:
        new_params = {}
        new_params['lbound'] = float(cd_lbound_entry.text())
        new_params['ubound'] = float(cd_ubound_entry.text())
        new_params['ulimit'] = float(cd_ulimit.text())
        new_params['chargecurrent'] = float(cd_chargecurrent_entry.text())/1e3 # convert uA to mA
        new_params['dischargecurrent'] = float(cd_dischargecurrent_entry.text())/1e3 # convert uA to mA
        new_params['waittime'] = str(cd_waittime_entry.text())
        new_params['discharge_waittime'] = str(cd_discharge_wait_entry.text())
        
        # Update discharge wait time global variable
        global cd_discharge_waittime
        cd_discharge_waittime = float(cd_discharge_wait_entry.text())
        
        # Validate critical parameters - log errors instead of showing dialogues
        if new_params['ubound'] < new_params['lbound']:
            log_message("Parameter update failed: Upper bound cannot be lower than lower bound")
            return
        
        if new_params['chargecurrent'] == 0. or new_params['dischargecurrent'] == 0.:
            log_message("Parameter update failed: Currents cannot be zero")
            return
            
        if new_params['chargecurrent'] * new_params['dischargecurrent'] > 0:
            log_message("Parameter update failed: Charge and discharge currents must have opposite signs")
            return
        
        # Update parameters
        cd_parameters.update(new_params)
        
        # Update auto-stop flag
        global cd_stop_after_next_discharge
        cd_stop_after_next_discharge = cd_autostop_checkbox.isChecked()
        
        log_message(f"Parameters updated during runtime - New bounds: {new_params['lbound']:.3f}V to {new_params['ubound']:.3f}V, Safety: {new_params['ulimit']:.3f}V")
        log_message(f"Updated currents - Charge: {new_params['chargecurrent']*1000:.1f}µA, Discharge: {new_params['dischargecurrent']*1000:.1f}µA")
        log_message(f"Updated wait times - Charge: {new_params['waittime']}s, Discharge: {new_params['discharge_waittime']}s")
        
        if cd_stop_after_next_discharge:
            log_message("Auto-stop enabled - will stop after next discharge cycle")
            
    except ValueError:
        log_message("Parameter update failed: One or more parameters could not be interpreted as a number")
    except Exception as e:
        log_message(f"Parameter update failed: {str(e)}")

def cd_discharge_wait_update():
    """Handle waiting time after discharge cycle"""
    global cd_waiting_after_discharge, cd_discharge_wait_start_time, cd_discharge_waittime
    
    if cd_discharge_wait_start_time == 0:
        set_cell_status(False)
        cd_current_cycle_entry_label.setText("Waiting after discharge (s)")
        cd_discharge_wait_start_time = timeit.default_timer()
        cd_waiting_after_discharge = True
        log_message(f"Starting {cd_discharge_waittime}s wait after discharge cycle")

    elapsed_time_since_discharge = timeit.default_timer() - cd_discharge_wait_start_time  
    cd_current_cycle_entry.setText("{}".format(int(elapsed_time_since_discharge)))
    
    if elapsed_time_since_discharge > cd_discharge_waittime:
        cd_discharge_wait_start_time = 0
        cd_waiting_after_discharge = False
        cd_current_cycle_entry_label.setText("Current half cycle")
        cd_current_cycle_entry.setText("%d"%cd_currentcycle)
        set_cell_status(True)
        log_message("Discharge wait period completed, resuming measurement")
        return True
    return False    

def current_to_string(currentrange, current_in_mA):
    """Format the measured current into a string with appropriate units and number of significant digits."""
    abs_value = abs(current_in_mA)
    if currentrange == 0:
        if abs_value <= 99.995:
            return u"%+6.2f mA"%current_in_mA
        else:
            return u"%+6.1f mA"%current_in_mA
    elif currentrange == 1:
        if abs_value <= 9.9995:
            return u"%+6.3f mA"%current_in_mA
        else:
            return u"%+6.2f mA"%current_in_mA
    elif currentrange == 2:
        if abs_value < 9.9995e-2:
            return u"%+06.2f µA"%(current_in_mA*1e3)
        else:
            return u"%+6.1f µA"%(current_in_mA*1e3)
    elif currentrange == 3:
        return u"%+6.3f µA"%(current_in_mA*1e3)
        
def potential_to_string(potential_in_V):
    """Format the measured potential into a string with appropriate units and number of significant digits."""
    return u"%+6.3f V"%potential_in_V

def twocomplement_to_decimal(msb, middlebyte, lsb):
    """Convert a 22-bit two-complement ADC value consisting of three bytes to a signed integer (see MCP3550 datasheet for details)."""
    ovh = (msb > 63) and (msb < 128) # Check for overflow high (B22 set)
    ovl = (msb > 127) # Check for overflow low (B23 set)
    combined_value = (msb%64)*2**16+middlebyte*2**8+lsb # Get rid of overflow bits
    if not ovh and not ovl:
        if msb > 31: # B21 set -> negative number
            answer = combined_value - 2**22
        else:
            answer = combined_value
    else: # overflow
        if msb > 127: # B23 set -> negative number
            answer = combined_value - 2**22
        else:
            answer = combined_value
    return answer

def decimal_to_dac_bytes(value):
    """Convert a floating-point number, ranging from -2**19 to 2**19-1, to three data bytes in the proper format for the DAC1220."""
    code = 2**19 + int(round(value)) # Convert the (signed) input value to an unsigned 20-bit integer with zero at midway
    code = np.clip(code, 0, 2**20 - 1) # If the input exceeds the boundaries of the 20-bit integer, clip it
    byte1 = code // 2**12
    byte2 = (code % 2**12) // 2**4
    byte3 = (code - byte1*2**12 - byte2*2**4)*2**4
    return bytes([byte1,byte2,byte3])
    
def dac_bytes_to_decimal(dac_bytes):
    """Convert three data bytes in the DAC1220 format to a 20-bit number ranging from -2**19 to 2**19-1."""
    code = 2**12*dac_bytes[0]+2**4*dac_bytes[1]+dac_bytes[2]/2**4
    return code - 2**19
    
def cv_sweep(time_elapsed, ustart, ustop, ubound, lbound, scanrate, n):
    """Generate the potential profile for a cyclic voltammetry sweep.
    
    Keyword arguments:
    time_elapsed -- the elapsed time
    ustart -- the start potential
    ustop -- the stop potential
    ubound -- the upper potential bound
    lbound -- the lower potential bound
    scanrate -- the scan rate
    n -- the number of scans
    
    Returns the potential as a function of the elapsed time; if the elapsed time exceeds the end of the CV sweep, returns None.
    """
    if scanrate < 0: # The rest of the function assumes a positive scan rate; a negative one is handled here by recursion
        try:
            return -cv_sweep(time_elapsed, -ustart, -ustop, -lbound, -ubound, -scanrate, n) # Re-run the function with inverted potentials and scan rates and invert the result
        except TypeError:
            return None # If the result of the inverted function is None, it cannot be inverted, so return None
    srt_0 = ubound-ustart # Potential difference to traverse in the initial stage (before potential reaches upper bound)
    srt_1 = (ubound-lbound)*2.*n # Potential difference to traverse in the "cyclic stage" (repeated scans from upper to lower bound and back)
    srt_2 = abs(ustop-ubound) # Potential difference to traverse in the final stage (from upper bound to stop potential)
    srtime = scanrate*time_elapsed # Linear potential sweep
    if srtime < srt_0: # Initial stage
        return ustart+srtime
    elif srtime < srt_0+srt_1: # Cyclic stage
        srtime = srtime - srt_0
        return lbound + abs((srtime)%(2*(ubound-lbound))-(ubound-lbound))
    elif srtime < srt_0+srt_1+srt_2: # Final stage
        srtime = srtime - srt_0 - srt_1
        if ustop > ubound:
            return ubound + srtime
        else:
            return ubound - srtime
    else:
        return None # CV finished

def charge_from_cv(time_arr, current_arr):
    """Integrate current as a function of time to calculate charge between zero crossings."""
    zero_crossing_indices = []
    charge_arr = []
    running_index = 0
    while running_index < len(current_arr):
        counter = 0
        while running_index < len(current_arr) and current_arr[running_index] >= 0.: # Iterate over a block of positive currents
            running_index += 1
            counter += 1
        if counter >= 10: # Check if the block holds at least 10 values (this makes the counting immune to noise around zero crossings)
            zero_crossing_indices.append(running_index-counter) # If so, append the index of the start of the block to the list of zero-crossing indices
        counter = 0
        while running_index < len(current_arr) and current_arr[running_index] <= 0.: # Do the same for a block of negative currents
            running_index += 1
            counter += 1
        if counter >= 10:
            zero_crossing_indices.append(running_index-counter)
    for index in range(0,len(zero_crossing_indices)-1): # Go over all zero crossings
        zc_index1 = zero_crossing_indices[index] # Start index
        zc_index2 = zero_crossing_indices[index+1] # End index
        charge_arr.append(np.trapezoid(current_arr[zc_index1:zc_index2],time_arr[zc_index1:zc_index2])*1000./3.6) # Integrate current over time using the trapezoid rule, convert coulomb to uAh
    return charge_arr

def make_groupbox_indicator(title_name, default_text):
    """Make a GUI box (used for the potential, current, and status indicators)."""
    label = QtWidgets.QLabel(text=default_text, alignment=QtCore.Qt.AlignCenter)
    box = QtWidgets.QGroupBox(title=title_name, flat=False)
    format_box_for_display(box)
    layout = QtWidgets.QVBoxLayout()
    layout.addWidget(label, 0, alignment=QtCore.Qt.AlignCenter)
    layout.setSpacing(0)
    layout.setContentsMargins(30,3,30,0)
    box.setLayout(layout)
    return label, box

def add_my_tab(tab_frame, tab_name):
    """Add a tab to the tab view."""
    widget = QtWidgets.QWidget()
    tab_frame.addTab(widget, tab_name)
    return widget

def format_box_for_display(box):
    """Adjust the appearance of a groupbox border for the status display."""
    color = box.palette().color(QtGui.QPalette.Background) # Get the background color
    r, g, b = int(0.9*color.red()), int(0.9*color.green()), int(0.9*color.blue()) # Make background 10% darker to make the border color
    box.setStyleSheet("QGroupBox { border: 1px solid rgb(%d,%d,%d); border-radius: 4px; margin-top: 0.5em; font-weight: normal; color: gray;} QGroupBox::title {subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px;}"%(r,g,b)) # Apply the border

def format_box_for_parameter(box):
    """Adjust the appearance of a groupbox border for parameter input."""
    color = box.palette().color(QtGui.QPalette.Background) # Get the background color
    r, g, b = int(0.7*color.red()), int(0.7*color.green()), int(0.7*color.blue()) # Make background 30% darker to make the border color
    box.setStyleSheet("QGroupBox { border: 1px solid rgb(%d,%d,%d); border-radius: 4px; margin-top: 0.5em; font-weight: bold} QGroupBox::title {subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px;}"%(r,g,b)) # Apply the border

def make_label_entry(parent, labelname):
    """Make a labelled input field for parameter input."""
    hbox = QtWidgets.QHBoxLayout()
    label = QtWidgets.QLabel(text=labelname)
    entry = QtWidgets.QLineEdit()
    hbox.addWidget(label)
    hbox.addWidget(entry)
    parent.addLayout(hbox)
    return label, entry

def custom_size_font(fontsize):
    """Return the default Qt font with a custom point size."""
    myfont = QtGui.QFont()
    myfont.setPointSize(fontsize)
    return myfont

def log_message(message):
    """Log a string to the message log."""
    statustext.appendPlainText(datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S] ")+message)
    statustext.ensureCursorVisible()

def connect_disconnect_usb():
    """Toggle the USB device between connected and disconnected states."""
    global dev, state
    if dev is not None: # If the device is already connected, then this function should disconnect it
        usb.util.dispose_resources(dev)
        dev = None
        state = States.NotConnected
        hardware_usb_connectButton.setText("Connect")
        log_message("USB Interface disconnected.")
        return
    # Otherwise, try to connect
    usb_vid_string = str(hardware_usb_vid.text())
    usb_pid_string = str(hardware_usb_pid.text())
    dev = usb.core.find(idVendor=int(usb_vid_string, 0), idProduct=int(usb_pid_string, 0))
    if dev is None:
        QtWidgets.QMessageBox.critical(mainwidget, "USB Device Not Found", "No USB device was found with VID %s and PID %s. Verify the vendor/product ID and check the USB connection."%(usb_vid_string,usb_pid_string))
    else:
        hardware_usb_connectButton.setText("Disconnect")
        log_message("USB Interface connected.")
        try:
            hardware_device_info_text.setText("Manufacturer: %s\nProduct: %s\nSerial #: %s"%(dev.manufacturer,dev.product,dev.serial_number))
            get_calibration()
            set_cell_status(False) # Cell off
            set_control_mode(False) # Potentiostatic control
            set_current_range() # Read current range from GUI
            state = States.Idle_Init # Start idle mode
        except ValueError:
            pass # In case the device is not yet calibrated

def on_usb_device_selected(index):
    """Handle selection change in the USB device dropdown."""
    global selected_usb_identifier
    if index >= 0:
        selected_usb_identifier = hardware_usb_device_dropdown.itemData(index)

def not_connected_errormessage():
    """Generate an error message stating that the device is not connected."""
    QtWidgets.QMessageBox.critical(mainwidget, "Not connected", "This command cannot be executed because the USB device is not connected. Press the \"Connect\" button and try again.")
    
def check_state(desired_states):
    """Check if the current state is in a given list. If so, return True; otherwise, show an error message and return False."""
    if state not in desired_states:
        if state == 0:
            not_connected_errormessage()
        else:
            QtWidgets.QMessageBox.critical(mainwidget, "Error", "This command cannot be executed in the current state.")
        return False
    else:
        return True

def send_command(command_string, expected_response, log_msg=None):
    """Send a command string to the USB device and check the response; optionally logs a message to the message log."""
    if dev is not None: # Make sure it's connected
        dev.write(0x01,command_string) # 0x01 = write address of EP1
        response = bytes(dev.read(0x81,64)) # 0x81 = read address of EP1
        if response != expected_response:
            QtWidgets.QMessageBox.critical(mainwidget, "Unexpected Response", "The command \"%s\" resulted in an unexpected response. The expected response was \"%s\"; the actual response was \"%s\""%(command_string,expected_response.decode("ascii"),response.decode("ascii")))
        else:
            if log_msg != None:
                log_message(log_msg)
        return True
    else:
        not_connected_errormessage()
        return False
        
def set_cell_status(cell_on_boolean):
    """Switch the cell connection (True = cell on, False = cell off)."""
    if cell_on_boolean:
        if send_command(b'CELL ON', b'OK'):
            cell_status_monitor.setText("CELL ON")
    else:
        if send_command(b'CELL OFF', b'OK'):
            cell_status_monitor.setText("CELL OFF")

def set_control_mode(galvanostatic_boolean):
    """Switch the control mode (True = galvanostatic, False = potentiostatic)."""
    if galvanostatic_boolean:
        if send_command(b'GALVANOSTATIC', b'OK'):
            control_mode_monitor.setText("GALVANOSTATIC")
    else:
        if send_command(b'POTENTIOSTATIC', b'OK'):
            control_mode_monitor.setText("POTENTIOSTATIC")

def set_current_range():
    """Switch the current range based on the GUI dropdown selection."""
    global currentrange
    index = hardware_manual_control_range_dropdown.currentIndex()
    commandstring = [b'RANGE 1',b'RANGE 2',b'RANGE 3',b'RANGE 4'][index]
    if send_command(commandstring, b'OK'):
        current_range_monitor.setText(current_range_list[index])
        currentrange = index

def auto_current_range():
    """Automatically switch the current range based on the measured current; returns a number of measurements to skip (to suppress artifacts)."""
    global currentrange, overcounter, undercounter
    if currentrange == 0:
        relativecurrent = abs(current/(2000.))
    elif currentrange == 1:
        relativecurrent = abs(current/(20.))
    elif currentrange == 2:
        relativecurrent = abs(current/(0.2))
    elif currentrange == 3:
        relativecurrent = abs(current/(0.002))
    if relativecurrent > 1.05 and currentrange != 0 and cv_range_checkboxes[currentrange-1].isChecked(): # Switch to higher current range (if possible) after three detections
        overcounter += 1
    else:
        overcounter = 0
    if relativecurrent < 0.0095 and currentrange != 3 and cv_range_checkboxes[currentrange+1].isChecked(): # Switch to lower current range (if possible) after three detections
        undercounter += 1
    else:
        undercounter = 0
    if overcounter > 3:
        currentrange -= 1
        hardware_manual_control_range_dropdown.setCurrentIndex(currentrange)
        set_current_range()
        overcounter = 0
        return 2 # Skip next two measurements to suppress artifacts
    elif undercounter > 3:
        currentrange += 1
        hardware_manual_control_range_dropdown.setCurrentIndex(currentrange)
        set_current_range()
        undercounter = 0
        return 2 # Skip next two measurements to suppress artifacts
    else:
        return 0
        
def current_range_from_current(current):
    """Return the current range that best corresponds to a given current."""
    current = abs(current)
    if current <= 0.002:
        return 3 # Lowest current range (2 uA)
    elif current <= 0.2:
        return 2 # Intermediate current range (200 uA)
    elif current <= 20.:
        return 1 # Intermediate current range (20 mA)
    else:
        return 0 # Highest current range (200 mA)

def get_next_enabled_current_range(desired_currentrange):
    """Return an enabled current range that best corresponds to a desired current range."""
    range_found = False
    found_currentrange = desired_currentrange
    for i in range(desired_currentrange,-1,-1): # Look for an enabled current range, going up in current range
        if cv_range_checkboxes[i].isChecked():
            found_currentrange = i
            range_found = True
            break
    if not range_found:
        for i in range(desired_currentrange,4): # Look for an enabled current range, going down in current range
            if cv_range_checkboxes[i].isChecked():
                found_currentrange = i
                break
    return found_currentrange

def set_offset():
    """Save offset values to the device's flash memory."""
    send_command(b'OFFSETSAVE '+decimal_to_dac_bytes(potential_offset)+decimal_to_dac_bytes(current_offset), b'OK', "Offset values saved to flash memory.")

def get_offset():
    """Retrieve offset values from the device's flash memory."""
    global potential_offset, current_offset
    if dev is not None: # Make sure it's connected
        dev.write(0x01,b'OFFSETREAD') # 0x01 = write address of EP1
        response = bytes(dev.read(0x81,64)) # 0x81 = read address of EP1
        if response != bytes([255,255,255,255,255,255]): # If no offset value has been stored, all bits will be set
            potential_offset = dac_bytes_to_decimal(response[0:3])
            current_offset = dac_bytes_to_decimal(response[3:6])    
            hardware_calibration_potential_offset.setText("%d"%potential_offset)
            hardware_calibration_current_offset.setText("%d"%current_offset)    
            log_message("Offset values read from flash memory.")
        else:
            log_message("No offset values were found in flash memory.")
    else:
        not_connected_errormessage()

def float_to_twobytes(value):
    """Convert a floating-point number ranging from -2^15 to 2^15-1 to a 16-bit representation stored in two bytes."""
    code = 2**15 + int(round(value))
    code = np.clip(code, 0, 2**16 - 1) # If the code exceeds the boundaries of a 16-bit integer, clip it
    byte1 = code // 2**8
    byte2 = code % 2**8
    return bytes([byte1,byte2])

def twobytes_to_float(bytes_in):
    """Convert two bytes to a number ranging from -2^15 to 2^15-1."""
    code = 2**8*bytes_in[0]+bytes_in[1]
    return float(code - 2**15)

def set_shunt_calibration():
    """Save shunt calibration values to the device's flash memory."""
    send_command(b'SHUNTCALSAVE '+float_to_twobytes((shunt_calibration[0]-1.)*1e5)+float_to_twobytes((shunt_calibration[1]-1.)*1e5)+float_to_twobytes((shunt_calibration[2]-1.)*1e5)+float_to_twobytes((shunt_calibration[3]-1.)*1e5), b'OK', "Shunt calibration values saved to flash memory.")

def get_shunt_calibration():
    """Retrieve shunt calibration values from the device's flash memory."""
    if dev is not None: # Make sure it's connected
        dev.write(0x01,b'SHUNTCALREAD') # 0x01 = write address of EP1
        response = bytes(dev.read(0x81,64)) # 0x81 = read address of EP1
        if response != bytes([255,255,255,255,255,255]): # If no calibration value has been stored, all bits are set
            for i in range(0,4):
                shunt_calibration[i] = 1.+twobytes_to_float(response[2*i:2*i+2])/1e5 # Yields an adjustment range from 0.672 to 1.328
                hardware_calibration_shuntvalues[i].setText("%.4f"%shunt_calibration[i])
            log_message("Shunt calibration values read from flash memory.")
        else:
            log_message("No shunt calibration values were found in flash memory.")
    else:
        not_connected_errormessage()

def zero_offset():
    """Calculate offset values in order to zero the potential and current."""
    if not check_state([States.Idle]):
        return # Device needs to be in the idle state for this
    pot_offs = int(round(np.average(list(last_raw_potential_values)))) # Average potential offset
    cur_offs = int(round(np.average(list(last_raw_current_values)))) # Average current offset
    hardware_calibration_potential_offset.setText("%d"%pot_offs)
    hardware_calibration_current_offset.setText("%d"%cur_offs)
    offset_changed_callback()

def offset_changed_callback():
    """Set the potential and current offset from the input fields."""
    global potential_offset, current_offset
    try:
        potential_offset = int(hardware_calibration_potential_offset.text())
        hardware_calibration_potential_offset.setStyleSheet("")
    except ValueError: # If the input field cannot be interpreted as a number, color it red
        hardware_calibration_potential_offset.setStyleSheet("QLineEdit { background: red; }")
    try:
        current_offset = int(hardware_calibration_current_offset.text())
        hardware_calibration_current_offset.setStyleSheet("")
    except ValueError: # If the input field cannot be interpreted as a number, color it red
        hardware_calibration_current_offset.setStyleSheet("QLineEdit { background: red; }")

def shunt_calibration_changed_callback():
    """Set the shunt calibration values from the input fields."""
    for i in range(0,4):
        try:
            shunt_calibration[i] = float(hardware_calibration_shuntvalues[i].text())
            hardware_calibration_shuntvalues[i].setStyleSheet("")
        except ValueError: # If the input field cannot be interpreted as a number, color it red
            hardware_calibration_shuntvalues[i].setStyleSheet("QLineEdit { background: red; }")

def set_dac_calibration():
    """Save DAC calibration values to the DAC and the device's flash memory."""
    try:
        dac_offset = int(hardware_calibration_dac_offset.text())
        hardware_calibration_dac_offset.setStyleSheet("")
    except ValueError: # If the input field cannot be interpreted as a number, color it red
        hardware_calibration_dac_offset.setStyleSheet("QLineEdit { background: red; }")
        return
    try:
        dac_gain = int(hardware_calibration_dac_gain.text())
        hardware_calibration_dac_gain.setStyleSheet("")
    except ValueError: # If the input field cannot be interpreted as a number, color it red
        hardware_calibration_dac_gain.setStyleSheet("QLineEdit { background: red; }")
        return
    send_command(b'DACCALSET '+decimal_to_dac_bytes(dac_offset)+decimal_to_dac_bytes(dac_gain-2**19), b'OK', "DAC calibration saved to flash memory.")

def get_dac_calibration():
    """Retrieve DAC calibration values from the device's flash memory."""
    if dev is not None: # Make sure it's connected
        dev.write(0x01,b'DACCALGET') # 0x01 = write address of EP1
        response = bytes(dev.read(0x81,64)) # 0x81 = write address of EP1
        if response != bytes([255,255,255,255,255,255]): # If no calibration value has been stored, all bits are set
            dac_offset = dac_bytes_to_decimal(response[0:3])
            dac_gain = dac_bytes_to_decimal(response[3:6])+2**19
            hardware_calibration_dac_offset.setText("%d"%dac_offset)
            hardware_calibration_dac_gain.setText("%d"%dac_gain)
            log_message("DAC calibration read from flash memory.")
        else:
            log_message("No DAC calibration values were found in flash memory.")
    else:
        not_connected_errormessage()

def set_calibration():
    """Save all calibration values to the device's flash memory."""
    set_dac_calibration()
    set_offset()
    set_shunt_calibration()

def get_calibration():
    """Retrieve all calibration values from the device's flash memory."""
    get_dac_calibration()
    get_offset()
    get_shunt_calibration()

def dac_calibrate():
    """Activate the automatic DAC1220 calibration function and retrieve the results."""
    send_command(b'DACCAL', b'OK', "DAC calibration performed.")
    get_dac_calibration()

def set_output(value_units_index, value):
    """Output data to the DAC; units can be either V (index 0), mA (index 1), or raw counts (index 2)."""
    if value_units_index == 0:
        send_command(b'DACSET '+decimal_to_dac_bytes(value/13.4*2.**19+int(round(potential_offset/4.))), b'OK')
    elif value_units_index == 1:
        if currentrange == 0:
            send_command(b'DACSET '+decimal_to_dac_bytes(value/(250./shunt_calibration[currentrange])*2.**19+int(round(current_offset/4.))), b'OK')
        if currentrange == 1:
            send_command(b'DACSET '+decimal_to_dac_bytes(value/(25./shunt_calibration[currentrange])*2.**19+int(round(current_offset/4.))), b'OK')
        if currentrange == 2:
            send_command(b'DACSET '+decimal_to_dac_bytes(value/(0.25/shunt_calibration[currentrange])*2.**19+int(round(current_offset/4.))), b'OK')
        if currentrange == 3:
            send_command(b'DACSET '+decimal_to_dac_bytes(value/(0.0025/shunt_calibration[currentrange])*2.**19+int(round(current_offset/4.))), b'OK')
    elif value_units_index == 2:
        send_command(b'DACSET '+decimal_to_dac_bytes(value), b'OK')

def set_output_from_gui():
    """Output data to the DAC from the GUI input field (hardware tab, manual control)."""
    value_units_index = hardware_manual_control_output_dropdown.currentIndex()
    if value_units_index == 0: # Potential (V)
        try:
            value = float(hardware_manual_control_output_entry.text())        
        except ValueError:
            QtWidgets.QMessageBox.critical(mainwidget, "Not a number", "The value you have entered is not a floating-point number.")
            return
    elif value_units_index == 1: # Current (mA)
        try:
            value = float(hardware_manual_control_output_entry.text())
        except ValueError:
            QtWidgets.QMessageBox.critical(mainwidget, "Not a number", "The value you have entered is not a floating-point number.")
            return
    elif value_units_index == 2: # DAC Code
        try:
            value = int(hardware_manual_control_output_entry.text())
        except ValueError:
            QtWidgets.QMessageBox.critical(mainwidget, "Not a number", "The value you have entered is not an integer number.")
            return
    else:
        return
    set_output(value_units_index, value)

def wait_for_adcread():
    """Wait for the duration specified in the busyloop_interval."""
    if busyloop_interval == 0:
        return # On Linux/Mac, system timing is used instead of the busyloop
    else:
        time.sleep(busyloop_interval/2.) # Sleep for some time to prevent wasting too many CPU cycles
        app.processEvents() # Update the GUI
        while timeit.default_timer() < time_of_last_adcread + busyloop_interval:
            pass # Busy loop (this is the only way to get accurate timing on MS Windows)

def read_potential_current():
    """Read the most recent potential and current values from the device's ADC."""
    global potential, current, raw_potential, raw_current, time_of_last_adcread
    wait_for_adcread()
    time_of_last_adcread = timeit.default_timer()
    dev.write(0x01,b'ADCREAD') # 0x01 = write address of EP1
    response = bytes(dev.read(0x81,64)) # 0x81 = read address of EP1
    if response != b'WAIT': # 'WAIT' is received if a conversion has not yet finished
        raw_potential = twocomplement_to_decimal(response[0], response[1], response[2])
        raw_current = twocomplement_to_decimal(response[3], response[4], response[5])
        potential = (raw_potential-potential_offset)/2097152.*13.4 # Calculate potential in V, compensating for offset
        if currentrange == 0:# Calculate current in mA, taking current range into account and compensating for offset
            current = (raw_current-current_offset)/2097152.*250./(shunt_calibration[currentrange])
        elif currentrange == 1:
            current = (raw_current-current_offset)/2097152.*25./(shunt_calibration[currentrange])
        elif currentrange == 2:
            current = (raw_current-current_offset)/2097152.*0.25/(shunt_calibration[currentrange])
        elif currentrange == 3:
            current = (raw_current-current_offset)/2097152.*0.0025/(shunt_calibration[currentrange])
        potential_monitor.setText(potential_to_string(potential))
        current_monitor.setText(current_to_string(currentrange, current))
        if logging_enabled: # If enabled, all measurements are appended to an output file (even in idle mode)
            try:
                if log_file_handle is None: # avoids opening the log file on each write, only the first one
                    log_file_handle = open(hardware_log_filename.text(), 'a', 1)
                print("%.2f\t%e\t%e"%(time_of_last_adcread,potential,current*1e-3),file=log_file_handle) # Output tab-separated data containing time (in s), potential (in V), and current (in A)
            except:
                QtWidgets.QMessageBox.critical(mainwidget, "Logging error!", "Logging error!")
                hardware_log_checkbox.setChecked(False) # Disable logging in case of file errors

def idle_init():
    """Perform some necessary initialization before entering the Idle state."""
    global potential_plot_curve, current_plot_curve, legend, state
    plot_frame.clear()
    try:
        legend.scene().removeItem(legend) # Remove any previous legends
    except AttributeError:
        pass # In case the legend was already removed
    except NameError:
        pass # In case a legend has never been created
    plot_frame.setLabel('bottom', 'Sample #', units="")
    plot_frame.setLabel('left', 'Value', units="")
    legend = plot_frame.addLegend()
    plot_frame.enableAutoRange()
    plot_frame.setXRange(0,200,update=True)
    potential_plot_curve = plot_frame.plot(pen='g', name='Potential (V)')
    current_plot_curve = plot_frame.plot(pen='r', name='Current (mA)')
    state = States.Idle # Proceed to the Idle state

def update_live_graph():
    """Add newly measured potential and current values to their respective buffers and update the plot curves."""
    last_potential_values.append(potential)
    last_current_values.append(current)
    last_raw_potential_values.append(raw_potential)
    last_raw_current_values.append(raw_current)
    xvalues = range(last_potential_values.maxlen-len(last_potential_values),last_potential_values.maxlen)
    potential_plot_curve.setData(xvalues, list(last_potential_values))
    current_plot_curve.setData(xvalues, list(last_current_values))

def choose_file(file_entry_field, questionstring):
    """Open a file dialog and write the path of the selected file to a given entry field."""
    filedialog = QtWidgets.QFileDialog()
    file_entry_field.setText(filedialog.getSaveFileName(mainwidget, questionstring, "", "ASCII data (*.txt)",options=QtWidgets.QFileDialog.DontConfirmOverwrite))

def emergency_shutdown():
    """Check if emergency conditions (overpotential/overcurrent) are present, and if so, shut down the cell."""
    global current, state, log_file_handle, emergency_voltage_V, emergency_current_mA

    try:
        emergency_voltage_V = float(hardware_emergency_voltage_entry.text())
        emergency_current_mA = float(hardware_emergency_current_entry.text())
    except:
        return # Use previous values
    
    # Check for overpotential
    if abs(potential) > emergency_voltage_V:
        set_cell_status(False)
        log_message("EMERGENCY: Cell switched off due to overpotential (%.3f V)!"%potential)
    
    # Check for overcurrent  
    if abs(current) > emergency_current_mA:
        set_cell_status(False)
        log_message("EMERGENCY: Cell switched off due to overcurrent (%.1f mA)!"%current)
    
    # Additional safety check for charge/discharge upper limit during measurements
    if state == States.Measuring_CD:
        try:
            # Check if we're in a charge/discharge measurement and have safety limits
            if 'cd_parameters' in globals() and 'ulimit' in cd_parameters:
                if potential > cd_parameters['ulimit']:
                    set_cell_status(False)
                    log_message("EMERGENCY: Cell switched off due to exceeding upper safety limit (%.3f V > %.3f V)!"%(potential, cd_parameters['ulimit']))
        except:
            pass

    if log_file_handle:
        log_file_handle.close()
        log_file_handle = None
        
def toggle_logging(checkbox_state):
    """Enable or disable logging of measurements to a file based on the state of a checkbox (2 means checked)."""
    global logging_enabled, log_file_handle
    logging_enabled = (checkbox_state == 2)

    if logging_enabled:
        # Open file once, keep handle
        log_file_handle = open(hardware_log_filename.text(), 'a', 1)
    else:
        # Close when disabled
        if log_file_handle:
            log_file_handle.close()
            log_file_handle = None

def cv_getparams():
    """Retrieve the CV parameters from the GUI input fields and store them in a global dictionary. If succesful, return True."""
    global cv_parameters
    try:
        cv_parameters['lbound'] = float(cv_lbound_entry.text())
        cv_parameters['ubound'] = float(cv_ubound_entry.text())
        cv_parameters['startpot'] = float(cv_startpot_entry.text())
        cv_parameters['stoppot'] = float(cv_stoppot_entry.text())
        cv_parameters['scanrate'] = float(cv_scanrate_entry.text())/1e3 # Convert to V/s
        cv_parameters['numcycles'] = int(cv_numcycles_entry.text())
        cv_parameters['numsamples'] = int(cv_numsamples_entry.text())
        cv_parameters['filename'] = str(cv_file_entry.text())
        return True
    except ValueError:
        QtWidgets.QMessageBox.critical(mainwidget, "Not a number", "One or more parameters could not be interpreted as a number.")
        return False

def cv_validate_parameters():
    """Check if the chosen CV parameters make sense. If so, return True."""
    if cv_parameters['ubound'] < cv_parameters['lbound']:
        QtWidgets.QMessageBox.critical(mainwidget, "CV error", "The upper bound cannot be lower than the lower bound.")
        return False
    if cv_parameters['scanrate'] == 0:
        QtWidgets.QMessageBox.critical(mainwidget, "CV error", "The scan rate cannot be zero.")
        return False
    if (cv_parameters['scanrate'] > 0) and (cv_parameters['ubound'] < cv_parameters['startpot']):
        QtWidgets.QMessageBox.critical(mainwidget, "CV error", "For a positive scan rate, the start potential must be lower than the upper bound.")
        return False
    if (cv_parameters['scanrate'] < 0) and (cv_parameters['lbound'] > cv_parameters['startpot']):
        QtWidgets.QMessageBox.critical(mainwidget, "CV error", "For a negative scan rate, the start potential must be higher than the lower bound.")
        return False
    if cv_parameters['numsamples'] < 1:
        QtWidgets.QMessageBox.critical(mainwidget, "CV error", "The number of samples to average must be at least 1.")
        return False
    return True

def validate_file(filename):
    """Check if a filename can be written to. If so, return True."""
    if os.path.isfile(filename):
        if QtWidgets.QMessageBox.question(mainwidget, "File exists", "The specified output file already exists. Do you want to overwrite it?", QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return False
    try:
        tryfile = open(filename, 'w', 1)
        tryfile.close()
        return True
    except IOError:
        QtWidgets.QMessageBox.critical(mainwidget, "File error", "The specified output file path is not valid.")
        return False

def cv_scanrate_changed_callback():
    """Calculate a suggested number of samples to average based on the entered value for the CV scan rate."""
    try:
        cv_scanrate = float(cv_scanrate_entry.text())
        numsamples = int(20./abs(cv_scanrate))+1 # Aims for approx. one (averaged) measurement every 2 to 4 mV for scan rates up to 20 mV/s
        cv_numsamples_entry.setText("%d"%numsamples)
    except:
        pass # Don't do anything in case the entered scan rate value is invalid

def cv_start():
    """Initialize the CV measurement."""
    global cv_time_data, cv_potential_data, cv_current_data, cv_plot_curve, cv_outputfile, state, skipcounter
    if check_state([States.Idle,States.Stationary_Graph]) and cv_getparams() and cv_validate_parameters() and validate_file(cv_parameters['filename']):
        cv_outputfile = open(cv_parameters['filename'], 'w', 1) # 1 means line-buffered
        cv_outputfile.write("Elapsed time(s)\tPotential(V)\tCurrent(A)\n")
        set_output(0, cv_parameters['startpot'])
        set_control_mode(False) # Potentiostatic control
        hardware_manual_control_range_dropdown.setCurrentIndex(0) # Start at highest current range
        set_current_range()
        time.sleep(.1) # Allow DAC some time to settle
        cv_time_data = AverageBuffer(cv_parameters['numsamples']) # Holds averaged data for elapsed time
        cv_potential_data = AverageBuffer(cv_parameters['numsamples']) # Holds averaged data for potential
        cv_current_data = AverageBuffer(cv_parameters['numsamples']) # Holds averaged data for current
        set_cell_status(True) # Cell on
        time.sleep(.1) # Allow feedback loop some time to settle
        read_potential_current()
        time.sleep(.1)
        read_potential_current() # Two reads are necessary because each read actually returns the result of the previous conversion
        hardware_manual_control_range_dropdown.setCurrentIndex(get_next_enabled_current_range(current_range_from_current(current))) # Autorange based on the measured current
        set_current_range()
        time.sleep(.1)
        read_potential_current()
        time.sleep(.1)
        read_potential_current()
        hardware_manual_control_range_dropdown.setCurrentIndex(get_next_enabled_current_range(current_range_from_current(current))) # Another autorange, just to be sure
        set_current_range()
        preview_cancel_button.hide()
        try: # Set up the plotting area
            legend.scene().removeItem(legend)
        except AttributeError:
            pass
        plot_frame.clear()
        plot_frame.enableAutoRange()
        plot_frame.setLabel('bottom', 'Potential', units="V")
        plot_frame.setLabel('left', 'Current', units="A")
        cv_plot_curve = plot_frame.plot(pen='y') # Plot CV in yellow
        log_message("CV measurement started. Saving to: %s"%cv_parameters['filename'])
        state = States.Measuring_CV
        skipcounter = 2 # Skip first two data points to suppress artifacts
        cv_parameters['starttime'] = timeit.default_timer()

def cv_update():
    """Add a new data point to the CV measurement (should be called regularly)."""
    global state, skipcounter
    elapsed_time = timeit.default_timer()-cv_parameters['starttime']
    cv_output = cv_sweep(elapsed_time, cv_parameters['startpot'], cv_parameters['stoppot'], cv_parameters['ubound'], cv_parameters['lbound'], cv_parameters['scanrate'], cv_parameters['numcycles'])
    if cv_output == None: # This signifies the end of the CV scan
        cv_stop(interrupted=False)
    else:
        set_output(0, cv_output) # Output a new potential value
        read_potential_current() # Read new potential and current
        if skipcounter == 0: # Process new measurements
            cv_time_data.add_sample(elapsed_time)
            cv_potential_data.add_sample(potential)
            cv_current_data.add_sample(1e-3*current) # Convert from mA to A
            if len(cv_time_data.samples) == 0 and len(cv_time_data.averagebuffer) > 0: # Check if a new average was just calculated
                cv_outputfile.write("%e\t%e\t%e\n"%(cv_time_data.averagebuffer[-1],cv_potential_data.averagebuffer[-1],cv_current_data.averagebuffer[-1])) # Write it out
                cv_plot_curve.setData(cv_potential_data.averagebuffer,cv_current_data.averagebuffer) # Update the graph
            skipcounter = auto_current_range() # Update the graph
        else: # Wait until the required number of data points is skipped
            skipcounter -= 1

def cv_stop(interrupted=True):
    """Finish the CV measurement."""
    global state
    if check_state([States.Measuring_CV]):
        set_cell_status(False) # Cell off
        cv_outputfile.close()
        charge_arr = charge_from_cv(cv_time_data.averagebuffer, cv_current_data.averagebuffer) # Integrate current between zero crossings to produce list of inserted/extracted charges
        if interrupted:
            log_message("CV measurement interrupted. Calculated charges (in uAh): [" + ', '.join("%.2f"%value for value in charge_arr) + "]") # Show calculated charges in the message log
        else:
            log_message("CV measurement finished. Calculated charges (in uAh): [" + ', '.join("%.2f"%value for value in charge_arr) + "]") # Show calculated charges in the message log
        state = States.Stationary_Graph # Keep displaying the last plot until the user clicks a button
        preview_cancel_button.show()

def cv_preview():
    """Generate a preview of the CV potential profile in the plot window, based on the CV parameters currently entered in the GUI."""
    global state
    if check_state([States.Idle,States.Stationary_Graph]) and cv_getparams() and cv_validate_parameters():
        time_arr = [] # Initialize time array
        potential_arr = [] # Initialize potential array
        timeval = 0. # Initialize elapsed time
        timestep = abs((cv_parameters['ubound']-cv_parameters['lbound'])/100./cv_parameters['scanrate']) # Automatic timestep calculation, resulting in 100 potential steps between lower and upper bound
        cv_val = cv_sweep(timeval, cv_parameters['startpot'], cv_parameters['stoppot'], cv_parameters['ubound'], cv_parameters['lbound'], cv_parameters['scanrate'], cv_parameters['numcycles'])
        while cv_val != None: # Fill time and potential arrays with calculated values, loop until end of CV profile
            time_arr.append(timeval)
            potential_arr.append(cv_val)
            timeval += timestep
            cv_val = cv_sweep(timeval, cv_parameters['startpot'], cv_parameters['stoppot'], cv_parameters['ubound'], cv_parameters['lbound'], cv_parameters['scanrate'], cv_parameters['numcycles'])
        try:
            legend.scene().removeItem(legend)
        except AttributeError:
            pass
        plot_frame.clear()
        plot_frame.enableAutoRange()
        plot_frame.setLabel('bottom', 'Time', units='s')
        plot_frame.setLabel('left', 'Potential', units='V')
        plot_frame.plot(time_arr, potential_arr, pen='g')
        preview_cancel_button.show()
        state = States.Stationary_Graph # Keep displaying the CV preview until the user clicks a button
        
def preview_cancel():
    """Cancel the preview (stationary graph) state and go back to the idle state."""
    global state
    state = States.Idle_Init
    preview_cancel_button.hide()

def cv_get_ocp():
    """Insert the currently measured (open-circuit) potential into the start potential input field."""
    cv_startpot_entry.setText('%5.3f'%potential)
    
def cd_getparams():
    """Retrieve the charge/discharge parameters from the GUI input fields and store them in a global dictionary. If succesful, return True."""
    global cd_parameters
    try:
        cd_parameters['lbound'] = float(cd_lbound_entry.text())
        cd_parameters['ubound'] = float(cd_ubound_entry.text())
        cd_parameters['ulimit'] = float(cd_ulimit.text())
        cd_parameters['chargecurrent'] = float(cd_chargecurrent_entry.text())/1e3 # convert uA to mA
        cd_parameters['dischargecurrent'] = float(cd_dischargecurrent_entry.text())/1e3 # convert uA to mA
        cd_parameters['numcycles'] = int(cd_numcycles_entry.text())
        cd_parameters['numsamples'] = int(cd_numsamples_entry.text())
        cd_parameters['filename'] = str(cd_file_entry.text())
        cd_parameters['waittime'] = str(cd_waittime_entry.text())
        cd_parameters['discharge_waittime'] = str(cd_discharge_wait_entry.text())
        return True
    except ValueError:
        QtWidgets.QMessageBox.critical(mainwidget, "Not a number", "One or more parameters could not be interpreted as a number.")
        return False

def cd_validate_parameters():
    """Check if the chosen charge/discharge parameters make sense. If so, return True."""
    if cd_parameters['ubound'] < cd_parameters['lbound']:
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "The upper bound cannot be lower than the lower bound.")
        return False
    if cd_parameters['chargecurrent'] == 0.:
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "The charge current cannot be zero.")
        return False
    if cd_parameters['dischargecurrent'] == 0.:
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "The discharge current cannot be zero.")
        return False
    if (abs(cd_parameters['chargecurrent']) > 200000 or abs(cd_parameters['chargecurrent']) > 200000) :
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "The magnitude of charge/discharge currents cannot exceed 200 mA.")
        return False
    if cd_parameters['chargecurrent']*cd_parameters['dischargecurrent'] > 0:
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "Charge and discharge current must have opposite sign.")
        return False
    if cd_parameters['numcycles'] <= 0:
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "The number of half cycles must be positive and non-zero.")
        return False
    if cd_parameters['numsamples'] < 1:
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "The number of samples to average must be at least 1.")
        return False
    if cd_type_dropdown.currentIndex() == 0 and (cd_parameters['ubound'] < 0.0 or cd_parameters['ubound'] > 3.0):
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "The upper potential bound must be between 0 and 3.0 V.")
        return False
    return True

def cd_start():
    """Initialize the charge/discharge measurement."""
    global cd_charges, cd_currentsetpoint, cd_starttime, cd_currentcycle, cd_time_data, cd_potential_data, cd_current_data, cd_plot_curves, cd_outputfile_raw, cd_outputfile_capacities, state, time_from_last_arduino_update, cd_waittime
    if check_state([States.Idle,States.Stationary_Graph]) and cd_getparams() and cd_validate_parameters() and validate_file(cd_parameters['filename']):
        cd_currentcycle = 1
        cd_charges = []
        cd_plot_curves = []
        cd_outputfile_raw = open(cd_parameters['filename'], 'w', 1) # This file will contain time, potential, and current data
        cd_outputfile_raw.write("Elapsed time(s)\tPotential(V)\tCurrent(A)\n")
        base, extension = os.path.splitext(cd_parameters['filename'])
        cd_outputfile_capacities = open(base+'_capacities'+extension, 'w', 1) # This file will contain capacity data for each cycle
        cd_outputfile_capacities.write("Cycle number\tCharge capacity (Ah)\tDischarge capacity (Ah)\n")
        cd_currentsetpoint = cd_parameters['chargecurrent']
        cd_waittime = cd_parameters['waittime']
        hardware_manual_control_range_dropdown.setCurrentIndex(current_range_from_current(cd_currentsetpoint)) # Determine the proper current range for the current setpoint
        set_current_range() # Set new current range
        set_output(1, cd_currentsetpoint) # Set current to setpoint
        
        if cd_type_dropdown.currentIndex() == 2:
            set_output(0, cd_parameters['ulimit'])
            set_control_mode(False) # Potentiostat # FOR CONSTANT POTENTIAL
        else:
            set_control_mode(True) # Galvanostatic control
        
        time.sleep(0.2) # Allow DAC some time to settle
        cd_starttime = timeit.default_timer()
        cd_time_data = AverageBuffer(cd_parameters['numsamples']) # Holds averaged data for elapsed time
        cd_potential_data = AverageBuffer(cd_parameters['numsamples']) # Holds averaged data for potential
        cd_current_data = AverageBuffer(cd_parameters['numsamples']) # Holds averaged data for current
        set_cell_status(True) # Cell on
        preview_cancel_button.hide()
        try: # Set up the plotting area
            legend.scene().removeItem(legend)
        except AttributeError:
            pass
        plot_frame.clear()
        plot_frame.enableAutoRange()
        plot_frame.setLabel('bottom', 'Inserted/extracted charge', units="Ah")
        plot_frame.setLabel('left', 'Potential', units="V")
        cd_plot_curves.append(plot_frame.plot(pen='y')) # Draw potential as a function of charge in yellow
        log_message("Charge/discharge measurement started. Saving to: %s"%cd_parameters['filename'])
        cd_current_cycle_entry.setText("%d"%cd_currentcycle) # Indicate the current cycle number
        cd_current_entry.setText("0") # Indicate the current for current cycle in uAh
        state = States.Measuring_CD
        time_from_last_arduino_update = 0.0
        
        # Initialize runtime variables
        global cd_stop_after_next_discharge, cd_waiting_after_discharge, cd_discharge_wait_start_time, cd_discharge_waittime
        cd_stop_after_next_discharge = False
        cd_waiting_after_discharge = False
        cd_discharge_wait_start_time = 0
        cd_discharge_waittime = float(cd_parameters['discharge_waittime'])  # Get from parameters instead of GUI
        
        # Enable runtime controls after successful start
        cd_update_button.setEnabled(True)
        cd_autostop_checkbox.setEnabled(True)
        
        log_message("Runtime controls enabled - you can now update parameters during the measurement")
        
def on_cd_type_changed(value):
    if cd_type_dropdown.currentIndex() == 0:
        cd_chargecurrent_entry_label.setText(u"Charge current (µA)")
        cd_ubound_entry_label.setText("Upper bound (V)")
        cd_ulimit_label.setText("Upper safety limit (V)")
    elif cd_type_dropdown.currentIndex() == 1:
        cd_chargecurrent_entry_label.setText(u"Charge current (µA)")
        cd_ubound_entry_label.setText("Upper bound (uAh)")
        cd_ulimit_label.setText("Upper safety limit (V)")
    elif cd_type_dropdown.currentIndex() == 2:
        cd_chargecurrent_entry_label.setText(u"Min charge current (µA)")
        cd_ubound_entry_label.setText("Upper bound (uAh)")
        
        cd_ulimit_label.setText("Constant Potential Charge (V)")

def cd_wait_update():
    """Handle the waiting period between charge/discharge cycles."""
    global state, cd_currentsetpoint, cd_wait_start_time
    
    if cd_wait_start_time == 0:
        set_cell_status(False)
        cd_current_cycle_entry_label.setText("Waiting time (s)")
        cd_wait_start_time = timeit.default_timer()

    elapsed_time_since_charge = timeit.default_timer() - cd_wait_start_time  
    cd_current_cycle_entry.setText("{}".format(int(elapsed_time_since_charge)))
   
    

def cd_update():
    """Add a new data point to the charge/discharge measurement (should be called regularly)."""
    global cd_currentsetpoint, cd_currentcycle, state, time_from_last_arduino_update, cd_wait_start_time, cd_waittime, cd_waiting_after_discharge, cd_stop_after_next_discharge, cd_discharge_wait_start_time
    elapsed_time = timeit.default_timer()-cd_starttime
    charge_milli = 0.0  # Initialize to prevent undefined variable errors
    
    # Handle waiting after charge cycle
    if cd_wait_start_time > 0:
        cd_wait_update()
        if timeit.default_timer() - cd_wait_start_time > int(cd_waittime):
            cd_wait_start_time = 0
            cd_current_cycle_entry_label.setText("Current half cycle")
            cd_current_cycle_entry.setText("%d"%cd_currentcycle)
            set_cell_status(True)
    
    # NEW: Handle waiting after discharge cycle
    if cd_waiting_after_discharge:
        if cd_discharge_wait_update():
            cd_waiting_after_discharge = False
        # Continue with data reading during discharge wait (don't return)
                     
    if cd_currentcycle > cd_parameters['numcycles']: # End of charge/discharge measurements
        cd_stop(interrupted=False)
    else: # Continue charge/discharge measurement process
        read_potential_current() # Read new potential and current
        
        
        cd_time_data.add_sample(elapsed_time)
        cd_potential_data.add_sample(potential)
        cd_current_data.add_sample(1e-3*current) # Convert mA to A
        if len(cd_time_data.samples) == 0 and len(cd_time_data.averagebuffer) > 0: # A new average was just calculated
            cd_outputfile_raw.write("%e\t%e\t%e\n"%(cd_time_data.averagebuffer[-1],cd_potential_data.averagebuffer[-1],cd_current_data.averagebuffer[-1])) # Write it out
            charge = np.absolute(scipy.integrate.cumulative_trapezoid(cd_current_data.averagebuffer,cd_time_data.averagebuffer,initial=0.)/3600.) # Cumulative charge in Ah
            cd_plot_curves[cd_currentcycle-1].setData(charge,cd_potential_data.averagebuffer) # Update the graph
            charge_milli = charge[-1]*1.0E6
            cd_current_entry.setText("{}".format(charge_milli)) # Indicate next cycle
        
        # CRITICAL FIX: Check for various cutoff conditions with ulimit safety check for ALL modes
        # Skip cutoff checks during waiting periods (but keep safety checks)
        if cd_wait_start_time > 0 or cd_waiting_after_discharge:
            # During waiting periods, skip all cutoff checks
            cutoff_triggered = False
        else:
            # Normal cutoff logic when not waiting
            cutoff_triggered = False
            cutoff_reason = ""
            
            # CHARGE PHASE (positive current): Check upper bounds including ulimit
            if cd_currentsetpoint > 0:  # CHARGE PHASE
                # Check ulimit for ALL modes (this was the original bug - it was only in mode 1)
                if potential > cd_parameters['ulimit']:
                    cutoff_triggered = True
                    cutoff_reason = f"Charge safety limit (ulimit) reached: {potential:.3f}V > {cd_parameters['ulimit']:.3f}V"
                    log_message(f"ulimit triggered during charge - will switch to discharge")
                
                # Check mode-specific upper bounds
                elif cd_type_dropdown.currentIndex() == 0:  # Potential cutoff mode
                    if potential > cd_parameters['ubound']:
                        cutoff_triggered = True
                        cutoff_reason = f"Upper potential bound reached: {potential:.3f}V > {cd_parameters['ubound']:.3f}V"
                
                elif cd_type_dropdown.currentIndex() == 1:  # Charge cutoff mode
                    if charge_milli > cd_parameters['ubound']:
                        cutoff_triggered = True
                        cutoff_reason = f"Charge cutoff reached: {charge_milli:.1f}µAh > {cd_parameters['ubound']:.1f}µAh"
                
                elif cd_type_dropdown.currentIndex() == 2:  # Potential + Charge cutoff mode
                    if charge_milli > cd_parameters['ubound']:
                        cutoff_triggered = True
                        cutoff_reason = f"Charge cutoff reached: {charge_milli:.1f}µAh > {cd_parameters['ubound']:.1f}µAh"
                    elif charge_milli > 1 and current < cd_parameters['chargecurrent']:
                        cutoff_triggered = True
                        cutoff_reason = f"Current dropped below threshold: {current:.1f}mA < {cd_parameters['chargecurrent']:.1f}mA"
            
            # DISCHARGE PHASE (negative current): ONLY check lower bounds
            elif cd_currentsetpoint < 0:  # DISCHARGE PHASE
                if potential < cd_parameters['lbound']:
                    cutoff_triggered = True
                    cutoff_reason = f"Discharge completed - lower potential bound reached: {potential:.3f}V < {cd_parameters['lbound']:.3f}V"
                # NOTE: ulimit and other upper thresholds are ignored during discharge
        
        # Log the cutoff reason for debugging
        if cutoff_triggered:
            log_message(f"Cutoff triggered: {cutoff_reason}")
        
        if cutoff_triggered: # A cutoff condition has been reached
            
            # Determine current phase based on setpoint sign (more reliable than exact comparison)
            currently_charging = cd_currentsetpoint > 0
            
            if currently_charging: # Switch from charge to discharge phase                                     
                cd_currentsetpoint = cd_parameters['dischargecurrent']
                if cd_type_dropdown.currentIndex() == 2:
                    set_control_mode(True) #set galvanostat
                    
                #wait between cycles to avoid polarization    
                cd_wait_start_time = 0
                cd_wait_update()
                log_message(f"Switching to discharge phase after charge cutoff: {cutoff_reason}")
            else: # Switch from discharge to charge phase
                # Check if auto-stop is enabled and we just finished a discharge cycle
                if cd_stop_after_next_discharge:
                    log_message("Auto-stop triggered after discharge cycle - stopping measurement")
                    cd_stop(interrupted=False)
                    return
                
                cd_currentsetpoint = cd_parameters['chargecurrent']
                if cd_type_dropdown.currentIndex() == 2:
                    set_control_mode(False) #set potentiostat
                
                # NEW: Add waiting after discharge cycle
                cd_discharge_wait_start_time = 0
                cd_waiting_after_discharge = True
                log_message(f"Switching to charge phase after discharge cutoff: {cutoff_reason}")
                    
            charge_milli = 0.0
            hardware_manual_control_range_dropdown.setCurrentIndex(current_range_from_current(cd_currentsetpoint)) # Determine the proper current range for the new setpoint
            set_current_range() # Set new current range
            
            if cd_type_dropdown.currentIndex() == 2:
                if cd_currentsetpoint > 0:
                    set_output(0, cd_parameters['ulimit'])
                else:
                    set_output(1, cd_currentsetpoint)  # Set current to setpoint        
            else:
                set_output(1, cd_currentsetpoint)  # Set current to setpoint

            cd_plot_curves.append(plot_frame.plot(pen='y')) # Start a new plot curve and append it to the plot area (keeping the old ones as well)
            cd_charges.append(np.absolute(np.trapezoid(cd_current_data.averagebuffer,cd_time_data.averagebuffer)/3600.)) # Cumulative charge in Ah
            if cd_currentcycle % 2 == 0: # Write out the charge and discharge capacities after both a charge and discharge phase (i.e. after cycle 2, 4, 6...)
                cd_outputfile_capacities.write("%d\t%e\t%e\n"%(cd_currentcycle/2,cd_charges[cd_currentcycle-2],cd_charges[cd_currentcycle-1]))
            for data in [cd_time_data, cd_potential_data, cd_current_data]: # Clear average buffers to prepare them for the next cycle
                data.clear()
            cd_currentcycle += 1 # Next cycle
            cd_current_cycle_entry.setText("%d"%cd_currentcycle) # Indicate next cycle

def cd_stop(interrupted=True):
    """Finish the charge/discharge measurement."""
    global state
    if check_state([States.Measuring_CD]):
        set_cell_status(False) # Cell off
        cd_outputfile_raw.close()
        cd_outputfile_capacities.close()
        if interrupted:
            log_message("Charge/discharge measurement interrupted. Calculated charges (in uAh): [" + ', '.join("%.2f"%(value*1e6) for value in cd_charges) + "]") # Print list of inserted/extracted charges to the message log
        else:
            log_message("Charge/discharge measurement finished. Calculated charges (in uAh): [" + ', '.join("%.2f"%(value*1e6) for value in cd_charges) + "]") # Print list of inserted/extracted charges to the message log
        cd_current_cycle_entry.setText("") # Clear cycle indicator
        
        # Disable runtime controls
        cd_update_button.setEnabled(False)
        cd_autostop_checkbox.setEnabled(False)
        cd_autostop_checkbox.setChecked(False)
        
        # Reset runtime variables
        global cd_stop_after_next_discharge, cd_waiting_after_discharge, cd_discharge_wait_start_time
        cd_stop_after_next_discharge = False
        cd_waiting_after_discharge = False
        cd_discharge_wait_start_time = 0

        state = States.Stationary_Graph # Keep displaying the last plot until the user clicks a button
        preview_cancel_button.show()

def rate_getparams():
    """Retrieve the rate testing parameters from the GUI input fields and store them in a global dictionary. If succesful, return True."""
    global rate_parameters
    try:
        rate_parameters['lbound'] = float(rate_lbound_entry.text())
        rate_parameters['ubound'] = float(rate_ubound_entry.text())
        rate_parameters['one_c_current'] = float(rate_capacity_entry.text())/1e3 # Convert uA to mA
        rate_parameters['crates'] = [float(x) for x in rate_crates_entry.text().split(",")]
        rate_parameters['currents'] = [rate_parameters['one_c_current']*rc for rc in rate_parameters['crates']]
        rate_parameters['numcycles'] = int(rate_numcycles_entry.text())
        rate_parameters['numsamples'] = int(cd_numsamples_entry.text())
        rate_parameters['filename'] = str(rate_file_entry.text())
        return True
    except ValueError:
        QtWidgets.QMessageBox.critical(mainwidget, "Not a number", "One or more parameters could not be interpreted as a number.")
        return False

def rate_validate_parameters():
    """Check if the chosen charge/discharge parameters make sense. If so, return True."""
    if rate_parameters['ubound'] < rate_parameters['lbound']:
        QtWidgets.QMessageBox.critical(mainwidget, "Rate testing error", "The upper bound cannot be lower than the lower bound.")
        return False
    if 0. in rate_parameters['currents']:
        QtWidgets.QMessageBox.critical(mainwidget, "Rate testing error", "The charge/discharge current cannot be zero.")
        return False
    if rate_parameters['numcycles'] <= 0:
        QtWidgets.QMessageBox.critical(mainwidget, "Charge/discharge error", "The number of half cycles must be positive and non-zero.")
        return False
    return True

def rate_start():
    """Initialize the rate testing measurement."""
    global state, crate_index, rate_halfcycle_countdown, rate_chg_charges, rate_dis_charges, rate_outputfile_raw, rate_outputfile_capacities, rate_starttime, rate_time_data, rate_potential_data, rate_current_data, rate_plot_scatter_chg, rate_plot_scatter_dis, legend
    if check_state([States.Idle,States.Stationary_Graph]) and rate_getparams() and rate_validate_parameters() and validate_file(rate_parameters['filename']):
        crate_index = 0 # Index in the list of C-rates
        rate_halfcycle_countdown = 2*rate_parameters['numcycles'] # Holds amount of remaining half cycles
        rate_chg_charges = [] # List of measured charge capacities
        rate_dis_charges = [] # List of measured discharge capacities
        rate_outputfile_raw = open(rate_parameters['filename'], 'w', 1) # This file will contain time, potential, and current data
        rate_outputfile_raw.write("Elapsed time(s)\tPotential(V)\tCurrent(A)\n")
        base, extension = os.path.splitext(rate_parameters['filename'])
        rate_outputfile_capacities = open(base+'_capacities'+extension, 'w', 1) # This file will contain capacity data for each C-rate
        rate_outputfile_capacities.write("C-rate\tCharge capacity (Ah)\tDischarge capacity (Ah)\n")
        rate_current = rate_parameters['currents'][crate_index] if rate_halfcycle_countdown%2 == 0 else -rate_parameters['currents'][crate_index] # Apply positive current for odd half cycles (charge phase) and negative current for even half cycles (discharge phase)
        hardware_manual_control_range_dropdown.setCurrentIndex(current_range_from_current(rate_current)) # Determine the proper current range for the current setpoint
        set_current_range()  # Set new current range
        set_output(1, rate_current) # Set current to setpoint
        set_control_mode(True) # Galvanostatic control
        time.sleep(.2) # Allow DAC some time to settle
        rate_starttime = timeit.default_timer()
        numsamples = max(1,int(36./rate_parameters['crates'][crate_index]))
        rate_time_data = AverageBuffer(numsamples) # Holds averaged data for elapsed time
        rate_potential_data = AverageBuffer(numsamples) # Holds averaged data for potential
        rate_current_data = AverageBuffer(numsamples) # Holds averaged data for current
        set_cell_status(True) # Cell on
        preview_cancel_button.hide()
        try: # Set up the plotting area
            legend.scene().removeItem(legend)
        except AttributeError:
            pass
        plot_frame.clear()
        legend = plot_frame.addLegend()
        plot_frame.enableAutoRange()
        plot_frame.setLabel('bottom', 'C-rate')
        plot_frame.setLabel('left', 'Inserted/extracted charge', units="Ah")
        rate_plot_scatter_chg = plot_frame.plot(symbol='o', pen=None, symbolPen='r', symbolBrush='r', name='Charge') # Plot charge capacity as a function of C-rate with red circles
        rate_plot_scatter_dis = plot_frame.plot(symbol='o', pen=None, symbolPen=(100,100,255), symbolBrush=(100,100,255), name='Discharge') # Plot discharge capacity as a function of C-rate with blue circles
        log_message("Rate testing started. Saving to: %s"%rate_parameters['filename'])
        rate_current_crate_entry.setText("%d"%rate_parameters['crates'][crate_index]) # Indicate the current C-rate
        state = States.Measuring_Rate

def rate_update():
    """Add a new data point to the rate testing measurement (should be called regularly)."""
    global state, crate_index, rate_halfcycle_countdown
    elapsed_time = timeit.default_timer()-rate_starttime
    read_potential_current()
    rate_time_data.add_sample(elapsed_time)
    rate_potential_data.add_sample(potential)
    rate_current_data.add_sample(1e-3*current) # Convert mA to A
    if len(rate_time_data.samples) == 0 and len(rate_time_data.averagebuffer) > 0: # A new average was just calculated
        rate_outputfile_raw.write("%e\t%e\t%e\n"%(rate_time_data.averagebuffer[-1],rate_potential_data.averagebuffer[-1],rate_current_data.averagebuffer[-1])) # Write it out
    if (rate_halfcycle_countdown%2 == 0 and potential > rate_parameters['ubound']) or (rate_halfcycle_countdown%2 != 0 and potential < rate_parameters['lbound']): # A potential cut-off has been reached
        rate_halfcycle_countdown -= 1
        if rate_halfcycle_countdown == 1: # Last charge cycle for this C-rate, so calculate and plot the charge capacity
            charge = np.absolute(scipy.integrate.trapezoid(rate_current_data.averagebuffer,rate_time_data.averagebuffer)/3600.) # Charge in Ah
            rate_chg_charges.append(charge)
            rate_plot_scatter_chg.setData(rate_parameters['crates'][0:crate_index+1], rate_chg_charges)
        elif rate_halfcycle_countdown == 0: # Last discharge cycle for this C-rate, so calculate and plot the discharge capacity, and go to the next C-rate
            charge = np.absolute(scipy.integrate.trapezoid(rate_current_data.averagebuffer,rate_time_data.averagebuffer)/3600.) # Charge in Ah
            rate_dis_charges.append(charge)
            rate_plot_scatter_dis.setData(rate_parameters['crates'][0:crate_index+1], rate_dis_charges)
            rate_outputfile_capacities.write("%e\t%e\t%e\n"%(rate_parameters['crates'][crate_index],rate_chg_charges[-1],rate_dis_charges[-1]))
            if crate_index == len(rate_parameters['crates'])-1: # Last C-rate was measured
                rate_stop(interrupted=False)
                return
            else: # New C-rate
                crate_index += 1
                rate_halfcycle_countdown = 2*rate_parameters['numcycles'] # Set the amount of remaining half cycles for the new C-rate
                set_output(1, 0.) # Set zero current while range switching
                hardware_manual_control_range_dropdown.setCurrentIndex(current_range_from_current(rate_parameters['currents'][crate_index])) # Determine the proper current range for the new setpoint
                set_current_range() # Set new current range
                numsamples = max(1,int(36./rate_parameters['crates'][crate_index])) # Set an appropriate amount of samples to average for the new C-rate; results in approx. 1000 points per curve
                for data in [rate_time_data, rate_potential_data, rate_current_data]:
                    data.number_of_samples_to_average = numsamples
        rate_current = rate_parameters['currents'][crate_index] if rate_halfcycle_countdown%2 == 0 else -rate_parameters['currents'][crate_index] # Apply positive current for odd half cycles (charge phase) and negative current for even half cycles (discharge phase)
        set_output(1, rate_current) # Set current to setpoint
        for data in [rate_time_data, rate_potential_data, rate_current_data]: # Clear average buffers to prepare them for the next cycle
            data.clear()
        rate_current_crate_entry.setText("%d"%rate_parameters['crates'][crate_index]) # Indicate the next C-rate

def rate_stop(interrupted=True):
    """Finish the rate testing measurement."""
    global state
    if check_state([States.Measuring_Rate]):
        state = States.Idle_Init
        set_cell_status(False) # Cell off
        rate_outputfile_raw.close()
        rate_outputfile_capacities.close()
        if interrupted:
            log_message("Rate testing interrupted.")
        else:
            log_message("Rate testing finished.")
        rate_current_crate_entry.setText("")  # Clear C-rate indicator
        state = States.Stationary_Graph # Keep displaying the last plot until the user clicks a button
        preview_cancel_button.show()
        
def m1_update():
    global arduino_dev, arduino_out_endpoint  
    
    if arduino_dev is None:
        log_message("Arduino not connected.")
        return
    
    # Only send command if pumps are toggled ON
    if not cd_mc_pumps_toggle.isChecked():
        return
    
    if cd_mlock_checkbox.isChecked():
        message = "<c,{}>".format(int(cd_mc_m1slider.value()*255.0/100.0))
    else:
        message = "<a,{}>".format(int(cd_mc_m1slider.value()*255.0/100.0))
    
    try:
        arduino_dev.write(arduino_out_endpoint, message.encode())
    except Exception as e:
        log_message("Arduino write error: " + str(e))
    
    cd_mc_m1value.setText(str(cd_mc_m1slider.value()))
    if cd_mlock_checkbox.isChecked():
        cd_mc_m2slider.setValue(cd_mc_m1slider.value())
        cd_mc_m2value.setText(str(cd_mc_m1slider.value()))
    
def m2_update():
    global arduino_dev, arduino_out_endpoint  
    
    if arduino_dev is None:
        log_message("Arduino not connected.")
        return
    
    # Only send command if pumps are toggled ON
    if not cd_mc_pumps_toggle.isChecked():
        return
    
    if cd_mlock_checkbox.isChecked():
        message = "<c,{}>".format(int(cd_mc_m2slider.value()*255.0/100.0))
    else:
        message = "<b,{}>".format(int(cd_mc_m2slider.value()*255.0/100.0))
    
    try:
        arduino_dev.write(arduino_out_endpoint, message.encode())
    except Exception as e:
        log_message("Arduino write error: " + str(e))
    
    cd_mc_m2value.setText(str(cd_mc_m2slider.value()))
    if cd_mlock_checkbox.isChecked():
        cd_mc_m1slider.setValue(cd_mc_m2slider.value())
        cd_mc_m1value.setText(str(cd_mc_m2slider.value()))
        
def connect_disconnect_arduino():
    global arduino_dev, arduino_out_endpoint, arduino_in_endpoint
    if arduino_dev is not None:
        usb.util.dispose_resources(arduino_dev)
        arduino_dev = None
        cd_mc_connect_button.setText("Connect")
        set_pump_controls_enabled(False)
        log_message("Arduino disconnected.")
        return
    
    if selected_arduino_identifier is None:
        QtWidgets.QMessageBox.warning(mainwidget, "No Device Selected", "Please select a device from the dropdown or click Refresh to scan for devices.")
        return
    
    bus, port_numbers, serial = selected_arduino_identifier
    
    arduino_dev = usb.core.find(
        idVendor=int(arduino_vid, 0),
        idProduct=int(arduino_pid, 0),
        custom_match=lambda dev: device_matches(dev, bus, port_numbers, serial)
    )
    
    if arduino_dev is None:
        QtWidgets.QMessageBox.critical(mainwidget, "Arduino Not Found", "Could not find the selected Arduino device.")
        return
    
    try:
        arduino_out_endpoint, arduino_in_endpoint = find_bulk_endpoints(arduino_dev)
        cd_mc_connect_button.setText("Disconnect")
        set_pump_controls_enabled(True)
        port_str = '.'.join(map(str, port_numbers))
        log_message(f"Arduino connected on bus {bus}, port {port_str}")
    except Exception as e:
        QtWidgets.QMessageBox.critical(mainwidget, "Connection Error", f"Failed to connect: {str(e)}")
        arduino_dev = None

def refresh_arduino_devices():
    """Scan for devices matching the current VID/PID and populate the dropdown."""
    global selected_arduino_port
    vid_string = str(cd_mc_vid.text())
    pid_string = str(cd_mc_pid.text())
    
    cd_mc_device_dropdown.clear()
    devices = find_devices_by_vid_pid(vid_string, pid_string)
    
    if not devices:
        cd_mc_device_dropdown.addItem("No devices found")
        selected_arduino_port = None
    else:
        for port, label in devices:
            cd_mc_device_dropdown.addItem(label, port)
        selected_arduino_port = devices[0][0]

def on_arduino_device_selected(index):
    """Handle selection change in the device dropdown."""
    global selected_arduino_identifier
    if index >= 0:
        selected_arduino_identifier = cd_mc_device_dropdown.itemData(index)

def create_device_selector(parent_layout, vid_default, pid_default, on_device_selected_callback):
    """Add a device selector row (dropdown + refresh button) to the given layout.
    
    Args:
        parent_layout: The QLayout to add the selector to
        vid_default: Default VID string (e.g. "0x1a86")
        pid_default: Default PID string (e.g. "0x7523")
        on_device_selected_callback: Function to call when dropdown selection changes
    
    Returns:
        Tuple of (vid_widget, pid_widget, device_dropdown, refresh_button)
    """
    vid_pid_hbox = QtWidgets.QHBoxLayout()
    vid_label, vid_widget = make_label_entry(vid_pid_hbox, "VID")
    vid_widget.setText(vid_default)
    pid_label, pid_widget = make_label_entry(vid_pid_hbox, "PID")
    pid_widget.setText(pid_default)
    parent_layout.addLayout(vid_pid_hbox)
    
    device_hbox = QtWidgets.QHBoxLayout()
    device_label = QtWidgets.QLabel("Device:")
    device_hbox.addWidget(device_label)
    device_dropdown = QtWidgets.QComboBox()
    device_hbox.addWidget(device_dropdown)
    refresh_button = QtWidgets.QPushButton("Refresh")
    refresh_button.setMaximumWidth(80)
    device_hbox.addWidget(refresh_button)
    parent_layout.addLayout(device_hbox)
    
    device_dropdown.currentIndexChanged.connect(on_device_selected_callback)
    
    return vid_widget, pid_widget, device_dropdown, refresh_button

def refresh_device_selector(device_dropdown, vid_widget, pid_widget, selected_identifier_global):
    """Scan for devices matching the current VID/PID and populate the dropdown.
    
    Args:
        device_dropdown: The QComboBox widget to populate
        vid_widget: The QLineEdit containing the VID
        pid_widget: The QLineEdit containing the PID
        selected_identifier_global: Name of the global variable to store selected device identifier
    """
    vid_string = str(vid_widget.text())
    pid_string = str(pid_widget.text())
    
    device_dropdown.clear()
    devices = find_devices_by_vid_pid(vid_string, pid_string)
    
    if not devices:
        device_dropdown.addItem("No devices found")
        globals()[selected_identifier_global] = None
    else:
        for identifier, label in devices:
            device_dropdown.addItem(label, identifier)
        globals()[selected_identifier_global] = devices[0][0]

def set_pump_controls_enabled(enabled):
    """Enable or disable all pump control widgets."""
    cd_mc_m1slider.setEnabled(enabled)
    cd_mc_m1_input.setEnabled(enabled)
    cd_mc_m2slider.setEnabled(enabled)
    cd_mc_m2_input.setEnabled(enabled)
    cd_mc_pumps_toggle.setEnabled(enabled)
    cd_mlock_checkbox.setEnabled(enabled)

def on_pumps_toggled(checked):
    """Handle pumps on/off toggle - controls both pumps."""
    global arduino_dev, arduino_out_endpoint
    if checked:
        cd_mc_pumps_toggle.setText("Turn Pumps Off")
        m1_update()
        m2_update()
    else:
        cd_mc_pumps_toggle.setText("Turn Pumps On")
        if arduino_dev is not None:
            try:
                arduino_dev.write(arduino_out_endpoint, "<c,0>".encode())
            except Exception as e:
                log_message("Arduino write error: " + str(e))

def on_m1_input_changed(input_widget):
    """Handle pump 1 manual input."""
    try:
        value = int(input_widget.text())
        if 0 <= value <= 100:
            cd_mc_m1slider.setValue(value)
            m1_update()
            input_widget.setStyleSheet("")
        else:
            input_widget.setStyleSheet("QLineEdit { background: red; }")
    except ValueError:
        input_widget.setStyleSheet("QLineEdit { background: red; }")

def on_m2_input_changed(input_widget):
    """Handle pump 2 manual input."""
    try:
        value = int(input_widget.text())
        if 0 <= value <= 100:
            cd_mc_m2slider.setValue(value)
            m2_update()
            input_widget.setStyleSheet("")
        else:
            input_widget.setStyleSheet("QLineEdit { background: red; }")
    except ValueError:
        input_widget.setStyleSheet("QLineEdit { background: red; }")

# Set up the GUI - Main Window
app = QtWidgets.QApplication([])
win = QtWidgets.QMainWindow()
win.setGeometry(300,300,1024,700)
win.setWindowTitle('USB potentiostat/galvanostat - v{}'.format(VERSION_NUMBER))
win.setWindowIcon(QtGui.QIcon('icon/icon.png'))

potential_monitor, potential_monitor_box = make_groupbox_indicator("Measured potential","+#.### V")
potential_monitor.setFont(QtGui.QFont("monospace", 32))
current_monitor, current_monitor_box = make_groupbox_indicator("Measured current","+#.### mA")
current_monitor.setFont(QtGui.QFont("monospace", 32))
potential_current_display_frame = QtWidgets.QHBoxLayout()
potential_current_display_frame.setSpacing(1)
potential_current_display_frame.setContentsMargins(0,0,0,0)
potential_current_display_frame.addWidget(potential_monitor_box)
potential_current_display_frame.addWidget(current_monitor_box)

mode_display_frame = QtWidgets.QHBoxLayout()
mode_display_frame.setSpacing(1)
mode_display_frame.setContentsMargins(0,0,0,5)
cell_status_monitor, cell_status_monitor_box = make_groupbox_indicator("Cell status","        ")
cell_status_monitor.setFont(custom_size_font(14))
control_mode_monitor, control_mode_monitor_box = make_groupbox_indicator("Control mode","             ")
control_mode_monitor.setFont(custom_size_font(14))
current_range_monitor, current_range_monitor_box = make_groupbox_indicator("Current range","     ")
current_range_monitor.setFont(custom_size_font(14))
mode_display_frame.addWidget(cell_status_monitor_box)
mode_display_frame.addWidget(control_mode_monitor_box)
mode_display_frame.addWidget(current_range_monitor_box)
pyqtgraph.setConfigOptions(foreground="#e5e5e5",background="#00304f")
plot_frame = pyqtgraph.PlotWidget()

display_plot_frame = QtWidgets.QVBoxLayout()
display_plot_frame.setSpacing(0)
display_plot_frame.setContentsMargins(0,0,0,0)
display_plot_frame.addLayout(potential_current_display_frame)
display_plot_frame.addLayout(mode_display_frame)
display_plot_frame.addWidget(plot_frame)

preview_cancel_vlayout = QtWidgets.QVBoxLayout(plot_frame)
preview_cancel_hlayout = QtWidgets.QHBoxLayout()
preview_cancel_vlayout.setAlignment(QtCore.Qt.AlignTop)
preview_cancel_vlayout.addLayout(preview_cancel_hlayout)
preview_cancel_hlayout.setAlignment(QtCore.Qt.AlignRight)
preview_cancel_button = QtWidgets.QPushButton("Back to live graph")
preview_cancel_button.clicked.connect(preview_cancel)
preview_cancel_hlayout.addWidget(preview_cancel_button)
preview_cancel_button.hide()

tab_frame = QtWidgets.QTabWidget()
tab_frame.setFixedWidth(305)

tab_names = ["Hardware","CV","Charge/disch.","Rate testing"]
tabs = [add_my_tab(tab_frame, tab_name) for tab_name in tab_names]

# Set up the GUI - Hardware tab
hardware_vbox = QtWidgets.QVBoxLayout()
hardware_vbox.setAlignment(QtCore.Qt.AlignTop)

hardware_usb_box = QtWidgets.QGroupBox(title="USB Interface", flat=False)
format_box_for_parameter(hardware_usb_box)
hardware_usb_box_layout = QtWidgets.QVBoxLayout()
hardware_usb_box.setLayout(hardware_usb_box_layout)

hardware_usb_vid, hardware_usb_pid, hardware_usb_device_dropdown, hardware_usb_refresh_button = create_device_selector(
    hardware_usb_box_layout, usb_vid, usb_pid, on_usb_device_selected
)

hardware_usb_connectButton = QtWidgets.QPushButton("Connect")
hardware_usb_connectButton.clicked.connect(connect_disconnect_usb)
hardware_usb_box_layout.addWidget(hardware_usb_connectButton)
hardware_usb_box_layout.setSpacing(5)
hardware_usb_box_layout.setContentsMargins(3,9,3,3)
hardware_vbox.addWidget(hardware_usb_box)

hardware_usb_refresh_button.clicked.connect(lambda: refresh_device_selector(hardware_usb_device_dropdown, hardware_usb_vid, hardware_usb_pid, "selected_usb_identifier"))

hardware_device_info_box = QtWidgets.QGroupBox(title="Device Information", flat=False)
format_box_for_parameter(hardware_device_info_box)
hardware_device_info_box_layout = QtWidgets.QVBoxLayout()
hardware_device_info_box.setLayout(hardware_device_info_box_layout)
hardware_device_info_text = QtWidgets.QLabel("Manufacturer: \nProduct: \nSerial #: ")
hardware_device_info_box_layout.addWidget(hardware_device_info_text)
hardware_device_info_box_layout.setSpacing(5)
hardware_device_info_box_layout.setContentsMargins(3,9,3,3)
hardware_vbox.addWidget(hardware_device_info_box)

hardware_calibration_box = QtWidgets.QGroupBox(title="Calibration", flat=False)
format_box_for_parameter(hardware_calibration_box)
hardware_calibration_box_layout = QtWidgets.QVBoxLayout()
hardware_calibration_box.setLayout(hardware_calibration_box_layout)
hardware_calibration_dac_hlayout = QtWidgets.QHBoxLayout()
hardware_calibration_box_layout.addLayout(hardware_calibration_dac_hlayout)
hardware_calibration_dac_vlayout = QtWidgets.QVBoxLayout()
hardware_calibration_dac_hlayout.addLayout(hardware_calibration_dac_vlayout)
hardware_calibration_dac_offset_label, hardware_calibration_dac_offset = make_label_entry(hardware_calibration_dac_vlayout, "DAC Offset")
hardware_calibration_dac_gain_label, hardware_calibration_dac_gain = make_label_entry(hardware_calibration_dac_vlayout, "DAC Gain")
hardware_calibration_dac_calibrate_button = QtWidgets.QPushButton("Auto\nCalibrate")
hardware_calibration_dac_calibrate_button.setMaximumHeight(100)
hardware_calibration_dac_calibrate_button.clicked.connect(dac_calibrate)
hardware_calibration_dac_hlayout.addWidget(hardware_calibration_dac_calibrate_button)

hardware_calibration_offset_hlayout = QtWidgets.QHBoxLayout()
hardware_calibration_box_layout.addLayout(hardware_calibration_offset_hlayout)
hardware_calibration_offset_vlayout = QtWidgets.QVBoxLayout()
hardware_calibration_offset_hlayout.addLayout(hardware_calibration_offset_vlayout)
hardware_calibration_potential_offset_label, hardware_calibration_potential_offset = make_label_entry(hardware_calibration_offset_vlayout, "Pot. Offset")
hardware_calibration_potential_offset.editingFinished.connect(offset_changed_callback)
hardware_calibration_potential_offset.setText("0")
hardware_calibration_current_offset_label, hardware_calibration_current_offset = make_label_entry(hardware_calibration_offset_vlayout, "Curr. Offset")
hardware_calibration_current_offset.editingFinished.connect(offset_changed_callback)
hardware_calibration_current_offset.setText("0")
hardware_calibration_adc_measure_button = QtWidgets.QPushButton("Auto\nZero")
hardware_calibration_adc_measure_button.setMaximumHeight(100)
hardware_calibration_adc_measure_button.clicked.connect(zero_offset)
hardware_calibration_offset_hlayout.addWidget(hardware_calibration_adc_measure_button)

hardware_calibration_shunt_resistor_layout = QtWidgets.QHBoxLayout()
hardware_calibration_box_layout.addLayout(hardware_calibration_shunt_resistor_layout)
hardware_calibration_shuntvalues_labels = []
hardware_calibration_shuntvalues = []
for i in range(1,5):
    hardware_calibration_shuntvalue_label, hardware_calibration_shuntvalue = make_label_entry(hardware_calibration_shunt_resistor_layout, "R%d"%i)
    hardware_calibration_shuntvalues_labels.append(hardware_calibration_shuntvalue_label)
    hardware_calibration_shuntvalues.append(hardware_calibration_shuntvalue)
for i in range(0,4):
    hardware_calibration_shuntvalues[i].editingFinished.connect(shunt_calibration_changed_callback)
    hardware_calibration_shuntvalues[i].setText("%.4f"%shunt_calibration[i])

hardware_calibration_button_layout = QtWidgets.QHBoxLayout()
hardware_calibration_get_button = QtWidgets.QPushButton("Load from device")
hardware_calibration_get_button.clicked.connect(get_calibration)
hardware_calibration_button_layout.addWidget(hardware_calibration_get_button)
hardware_calibration_set_button = QtWidgets.QPushButton("Save to device")
hardware_calibration_set_button.clicked.connect(set_calibration)
hardware_calibration_button_layout.addWidget(hardware_calibration_set_button)

hardware_calibration_box_layout.addLayout(hardware_calibration_button_layout)
hardware_calibration_box_layout.setSpacing(3)
hardware_calibration_box_layout.setContentsMargins(3,7,3,3)
hardware_vbox.addWidget(hardware_calibration_box)

hardware_manual_control_box = QtWidgets.QGroupBox(title="Manual Control", flat=False)
format_box_for_parameter(hardware_manual_control_box)
hardware_manual_control_box_layout = QtWidgets.QVBoxLayout()
hardware_manual_control_box.setLayout(hardware_manual_control_box_layout)

hardware_manual_control_cell_layout = QtWidgets.QHBoxLayout()
hardware_manual_control_cell_layout.addWidget(QtWidgets.QLabel("Cell connection"))
hardware_manual_control_cell_on_button = QtWidgets.QPushButton("On")
hardware_manual_control_cell_on_button.clicked.connect(lambda: set_cell_status(True))
hardware_manual_control_cell_layout.addWidget(hardware_manual_control_cell_on_button)
hardware_manual_control_cell_off_button = QtWidgets.QPushButton("Off")
hardware_manual_control_cell_off_button.clicked.connect(lambda: set_cell_status(False))
hardware_manual_control_cell_layout.addWidget(hardware_manual_control_cell_off_button)
hardware_manual_control_box_layout.addLayout(hardware_manual_control_cell_layout)

hardware_manual_control_mode_layout = QtWidgets.QHBoxLayout()
hardware_manual_control_mode_layout.addWidget(QtWidgets.QLabel("Mode"))
hardware_manual_control_mode_potentiostat_button = QtWidgets.QPushButton("Potentiostatic")
hardware_manual_control_mode_potentiostat_button.clicked.connect(lambda: set_control_mode(False))
hardware_manual_control_mode_layout.addWidget(hardware_manual_control_mode_potentiostat_button)
hardware_manual_control_mode_galvanostat_button = QtWidgets.QPushButton("Galvanostatic")
hardware_manual_control_mode_galvanostat_button.clicked.connect(lambda: set_control_mode(True))
hardware_manual_control_mode_layout.addWidget(hardware_manual_control_mode_galvanostat_button)
hardware_manual_control_box_layout.addLayout(hardware_manual_control_mode_layout)

hardware_manual_control_range_layout = QtWidgets.QHBoxLayout()
hardware_manual_control_range_layout.addWidget(QtWidgets.QLabel("Current range"))
hardware_manual_control_range_dropdown = QtWidgets.QComboBox()
hardware_manual_control_range_dropdown.addItems(current_range_list)
hardware_manual_control_range_layout.addWidget(hardware_manual_control_range_dropdown)
hardware_manual_control_range_set_button = QtWidgets.QPushButton("Set")
hardware_manual_control_range_set_button.clicked.connect(set_current_range)
hardware_manual_control_range_layout.addWidget(hardware_manual_control_range_set_button)
hardware_manual_control_box_layout.addLayout(hardware_manual_control_range_layout)

hardware_manual_control_output_layout = QtWidgets.QHBoxLayout()
hardware_manual_control_output_dropdown = QtWidgets.QComboBox()
hardware_manual_control_output_dropdown.addItems(units_list)
hardware_manual_control_output_layout.addWidget(hardware_manual_control_output_dropdown)
hardware_manual_control_output_entry = QtWidgets.QLineEdit()
hardware_manual_control_output_entry.returnPressed.connect(set_output_from_gui)
hardware_manual_control_output_layout.addWidget(hardware_manual_control_output_entry)
hardware_manual_control_output_set_button = QtWidgets.QPushButton("Set")
hardware_manual_control_output_set_button.clicked.connect(set_output_from_gui)
hardware_manual_control_output_layout.addWidget(hardware_manual_control_output_set_button)
hardware_manual_control_box_layout.addLayout(hardware_manual_control_output_layout)

hardware_manual_control_box_layout.setSpacing(5)
hardware_manual_control_box_layout.setContentsMargins(3,9,3,3)

hardware_vbox.addWidget(hardware_manual_control_box)

hardware_log_box = QtWidgets.QGroupBox(title="Log to file", flat=False)
format_box_for_parameter(hardware_log_box)
hardware_log_box_layout = QtWidgets.QHBoxLayout()
hardware_log_box.setLayout(hardware_log_box_layout)
hardware_log_checkbox = QtWidgets.QCheckBox("Log")
hardware_log_checkbox.stateChanged.connect(toggle_logging)
hardware_log_box_layout.addWidget(hardware_log_checkbox)
hardware_log_filename = QtWidgets.QLineEdit()
hardware_log_box_layout.addWidget(hardware_log_filename)
hardware_log_choose_button = QtWidgets.QPushButton("...")
hardware_log_choose_button.setFixedWidth(32)
hardware_log_choose_button.clicked.connect(lambda: choose_file(hardware_log_filename,"Choose where to save the log data"))
hardware_log_box_layout.addWidget(hardware_log_choose_button)

hardware_log_box_layout.setSpacing(5)
hardware_log_box_layout.setContentsMargins(3,9,3,3)

hardware_vbox.addWidget(hardware_log_box)

hardware_vbox.setSpacing(6)
hardware_vbox.setContentsMargins(3,3,3,3)

tabs[0].setLayout(hardware_vbox)

# Set up the GUI - CV tab
cv_vbox = QtWidgets.QVBoxLayout()
cv_vbox.setAlignment(QtCore.Qt.AlignTop)

cv_params_box = QtWidgets.QGroupBox(title="Cyclic voltammetry parameters", flat=False)
format_box_for_parameter(cv_params_box)
cv_params_layout = QtWidgets.QVBoxLayout()
cv_params_box.setLayout(cv_params_layout)
cv_lbound_entry_label, cv_lbound_entry = make_label_entry(cv_params_layout, "Lower bound (V)")
cv_ubound_entry_label, cv_ubound_entry = make_label_entry(cv_params_layout, "Upper bound (V)")

cv_hbox = QtWidgets.QHBoxLayout()
cv_label = QtWidgets.QLabel(text="Start potential (V)")
cv_startpot_entry = QtWidgets.QLineEdit()
cv_get_button = QtWidgets.QPushButton("OCP")
cv_get_button.setFont(custom_size_font(8))
cv_get_button.setFixedWidth(32)
cv_get_button.clicked.connect(cv_get_ocp)

cv_hbox.addWidget(cv_label)
cv_hbox.addWidget(cv_startpot_entry)
cv_hbox.addWidget(cv_get_button)
cv_params_layout.addLayout(cv_hbox)

cv_stoppot_entry_label, cv_stoppot_entry = make_label_entry(cv_params_layout, "Stop potential (V)")
cv_scanrate_entry_label, cv_scanrate_entry = make_label_entry(cv_params_layout, "Scan rate (mV/s)")
cv_scanrate_entry.editingFinished.connect(cv_scanrate_changed_callback)
cv_numcycles_entry_label, cv_numcycles_entry = make_label_entry(cv_params_layout, "Number of cycles")
cv_numsamples_entry_label, cv_numsamples_entry = make_label_entry(cv_params_layout, "Samples to average")
cv_numsamples_entry.setText("1")

cv_params_layout.setSpacing(6)
cv_params_layout.setContentsMargins(3,10,3,3)
cv_vbox.addWidget(cv_params_box)

cv_range_box = QtWidgets.QGroupBox(title="Autoranging", flat=False)
format_box_for_parameter(cv_range_box)
cv_range_layout = QtWidgets.QVBoxLayout()
cv_range_box.setLayout(cv_range_layout)
cv_range_checkboxes = []
for current in current_range_list:
    checkbox = QtWidgets.QCheckBox(current)
    cv_range_checkboxes.append(checkbox)
    cv_range_layout.addWidget(checkbox)
    checkbox.setChecked(True)

cv_range_layout.setSpacing(6)
cv_range_layout.setContentsMargins(3,10,3,3)
cv_vbox.addWidget(cv_range_box)

cv_file_box = QtWidgets.QGroupBox(title="Output data filename", flat=False)
format_box_for_parameter(cv_file_box)
cv_file_layout = QtWidgets.QVBoxLayout()
cv_file_box.setLayout(cv_file_layout)
cv_file_choose_layout = QtWidgets.QHBoxLayout()
cv_file_entry = QtWidgets.QLineEdit()
cv_file_choose_layout.addWidget(cv_file_entry)
cv_file_choose_button = QtWidgets.QPushButton("...")
cv_file_choose_button.setFixedWidth(32)
cv_file_choose_layout.addWidget(cv_file_choose_button)
cv_file_choose_button.clicked.connect(lambda: choose_file(cv_file_entry,"Choose where to save the CV measurement data"))
cv_file_layout.addLayout(cv_file_choose_layout)
cv_file_layout.setSpacing(6)
cv_file_layout.setContentsMargins(3,10,3,3)
cv_vbox.addWidget(cv_file_box)

cv_preview_button = QtWidgets.QPushButton("Preview sweep")
cv_preview_button.clicked.connect(cv_preview)
cv_vbox.addWidget(cv_preview_button)
cv_start_button = QtWidgets.QPushButton("Start cyclic voltammetry")
cv_start_button.clicked.connect(cv_start)
cv_vbox.addWidget(cv_start_button)
cv_stop_button = QtWidgets.QPushButton("Stop cyclic voltammetry")
cv_stop_button.clicked.connect(lambda: cv_stop(interrupted=True))
cv_vbox.addWidget(cv_stop_button)

cv_vbox.setSpacing(6)
cv_vbox.setContentsMargins(3,3,3,3)

tabs[1].setLayout(cv_vbox)

# Set up the GUI - charge/discharge tab
cd_vbox = QtWidgets.QVBoxLayout()
cd_vbox.setAlignment(QtCore.Qt.AlignTop)

cd_params_box = QtWidgets.QGroupBox(title="Charge/discharge parameters", flat=False)
format_box_for_parameter(cd_params_box)
cd_params_layout = QtWidgets.QVBoxLayout()
cd_params_box.setLayout(cd_params_layout)

cd_lbound_entry_label, cd_lbound_entry = make_label_entry(cd_params_layout, "Lower bound (V)")

cd_type_dropdown = QtWidgets.QComboBox()
cd_type_dropdown.addItems(["Charge to potential", "Charge to injected charge (uAh)", "Charge to injected charge at constant potential (uAh)"])
cd_type_dropdown.currentIndexChanged.connect(on_cd_type_changed)
cd_params_layout.addWidget(cd_type_dropdown)

cd_ubound_entry_label, cd_ubound_entry = make_label_entry(cd_params_layout, "Upper bound (V)")
cd_ulimit_label, cd_ulimit = make_label_entry(cd_params_layout, "Upper safety limit (V)")
cd_chargecurrent_entry_label, cd_chargecurrent_entry = make_label_entry(cd_params_layout, u"Charge current (µA)")
cd_dischargecurrent_entry_label, cd_dischargecurrent_entry = make_label_entry(cd_params_layout, u"Discharge current (µA)")
cd_numcycles_entry_label, cd_numcycles_entry = make_label_entry(cd_params_layout, "Number of half cycles")
cd_numsamples_entry_label, cd_numsamples_entry = make_label_entry(cd_params_layout, "Samples to average")
cd_numsamples_entry.setText("1")
cd_waittime_entry_label, cd_waittime_entry = make_label_entry(cd_params_layout, "Charge wait time (s)")
cd_waittime_entry.setText("30")
cd_discharge_wait_entry_label, cd_discharge_wait_entry = make_label_entry(cd_params_layout, "Discharge wait time (s)")
cd_discharge_wait_entry.setText("30")


cd_params_layout.setSpacing(6)
cd_params_layout.setContentsMargins(3,10,3,3)
cd_vbox.addWidget(cd_params_box)

cd_file_box = QtWidgets.QGroupBox(title="Output data filename", flat=False)
format_box_for_parameter(cd_file_box)
cd_file_layout = QtWidgets.QVBoxLayout()
cd_file_box.setLayout(cd_file_layout)
cd_file_choose_layout = QtWidgets.QHBoxLayout()
cd_file_entry = QtWidgets.QLineEdit()
cd_file_choose_layout.addWidget(cd_file_entry)
cd_file_choose_button = QtWidgets.QPushButton("...")
cd_file_choose_button.setFixedWidth(32)
cd_file_choose_layout.addWidget(cd_file_choose_button)
cd_file_choose_button.clicked.connect(lambda: choose_file(cd_file_entry,"Choose where to save the charge/discharge measurement data"))
cd_file_layout.addLayout(cd_file_choose_layout)
cd_file_layout.setSpacing(6)
cd_file_layout.setContentsMargins(3,10,3,3)
cd_vbox.addWidget(cd_file_box)

cd_start_button = QtWidgets.QPushButton("Start charge/discharge")
cd_start_button.clicked.connect(cd_start)
cd_vbox.addWidget(cd_start_button)
cd_stop_button = QtWidgets.QPushButton("Stop charge/discharge")
cd_stop_button.clicked.connect(lambda: cd_stop(interrupted=True))
cd_vbox.addWidget(cd_stop_button)

# NEW: Runtime Control Box
cd_runtime_box = QtWidgets.QGroupBox(title="Runtime Controls", flat=False)
format_box_for_parameter(cd_runtime_box)
cd_runtime_layout = QtWidgets.QVBoxLayout()
cd_runtime_box.setLayout(cd_runtime_layout)

# Update Parameters Button
cd_update_button = QtWidgets.QPushButton("Update Parameters During Run")
cd_update_button.clicked.connect(cd_update_parameters)
cd_runtime_layout.addWidget(cd_update_button)

# Auto-stop after discharge checkbox
cd_autostop_checkbox = QtWidgets.QCheckBox("Auto-stop after next discharge cycle")
cd_autostop_checkbox.setEnabled(False)  # Disabled until measurement starts
cd_runtime_layout.addWidget(cd_autostop_checkbox)

cd_runtime_layout.setSpacing(6)
cd_runtime_layout.setContentsMargins(3,10,3,3)
cd_vbox.addWidget(cd_runtime_box)

cd_vbox.setSpacing(6)
cd_vbox.setContentsMargins(3,3,3,3)

cd_info_box = QtWidgets.QGroupBox(title="Information", flat=False)
format_box_for_parameter(cd_info_box)
cd_info_layout = QtWidgets.QVBoxLayout()
cd_info_box.setLayout(cd_info_layout)

cd_current_cycle_entry_label, cd_current_cycle_entry = make_label_entry(cd_info_layout, "Current half cycle")
cd_current_cycle_entry.setReadOnly(True)

cd_current_entry_label, cd_current_entry = make_label_entry(cd_info_layout, "Current (uAh)")
cd_current_entry.setReadOnly(True)

cd_info_layout.setSpacing(6)
cd_info_layout.setContentsMargins(3,10,3,3)
cd_vbox.addWidget(cd_info_box)

## pump controls forms
cd_mc_box = QtWidgets.QGroupBox(title="Pump Controls", flat=False)
format_box_for_parameter(cd_mc_box)
cd_mc_layout = QtWidgets.QVBoxLayout()
cd_mc_box.setLayout(cd_mc_layout)

cd_mc_vid, cd_mc_pid, cd_mc_device_dropdown, cd_mc_refresh_button = create_device_selector(
    cd_mc_layout, arduino_vid, arduino_pid, on_arduino_device_selected
)

cd_mc_connect_button = QtWidgets.QPushButton("Connect")
cd_mc_connect_button.clicked.connect(connect_disconnect_arduino)
cd_mc_layout.addWidget(cd_mc_connect_button)

cd_mc_refresh_button.clicked.connect(lambda: refresh_device_selector(cd_mc_device_dropdown, cd_mc_vid, cd_mc_pid, "selected_arduino_port"))

cd_mlock_checkbox = QtWidgets.QCheckBox("Adjust pumps together")
cd_mc_layout.addWidget(cd_mlock_checkbox)
cd_mlock_checkbox.setChecked(True)

cd_mc_m1label = QtWidgets.QLabel()
cd_mc_m1label.setText("Positive Pump Control")
cd_mc_layout.addWidget(cd_mc_m1label)

cd_mc_m1slider_row = QtWidgets.QHBoxLayout()
cd_mc_m1slider = QtWidgets.QSlider()
cd_mc_m1slider.setOrientation(QtCore.Qt.Horizontal)
cd_mc_m1slider.setTickPosition(0)
cd_mc_m1slider.setMinimum(0)
cd_mc_m1slider.setMaximum(100)
cd_mc_m1slider.setTracking(False)
cd_mc_m1slider.valueChanged.connect(m1_update)
cd_mc_m1slider_row.addWidget(cd_mc_m1slider)

cd_mc_m1_input = QtWidgets.QLineEdit()
cd_mc_m1_input.setPlaceholderText("0-100")
cd_mc_m1_input.returnPressed.connect(lambda: on_m1_input_changed(cd_mc_m1_input))
cd_mc_m1slider_row.addWidget(cd_mc_m1_input)
cd_mc_layout.addLayout(cd_mc_m1slider_row)

cd_mc_m1value = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
cd_mc_m1value.setText("0")
cd_mc_layout.addWidget(cd_mc_m1value)

cd_mc_m2label = QtWidgets.QLabel()
cd_mc_m2label.setText("Negative Pump Control")
cd_mc_layout.addWidget(cd_mc_m2label)

cd_mc_m2slider_row = QtWidgets.QHBoxLayout()
cd_mc_m2slider = QtWidgets.QSlider()
cd_mc_m2slider.setOrientation(QtCore.Qt.Horizontal)
cd_mc_m2slider.setTickPosition(0)
cd_mc_m2slider.setMinimum(0)
cd_mc_m2slider.setMaximum(100)
cd_mc_m2slider.setTracking(False)
cd_mc_m2slider.valueChanged.connect(m2_update)
cd_mc_m2slider_row.addWidget(cd_mc_m2slider)

cd_mc_m2_input = QtWidgets.QLineEdit()
cd_mc_m2_input.setPlaceholderText("0-100")
cd_mc_m2_input.returnPressed.connect(lambda: on_m2_input_changed(cd_mc_m2_input))
cd_mc_m2slider_row.addWidget(cd_mc_m2_input)
cd_mc_layout.addLayout(cd_mc_m2slider_row)

cd_mc_m2value = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
cd_mc_m2value.setText("0")
cd_mc_layout.addWidget(cd_mc_m2value)

cd_mc_pumps_toggle = QtWidgets.QPushButton("Turn Pumps On")
cd_mc_pumps_toggle.setCheckable(True)
cd_mc_pumps_toggle.setChecked(False)
cd_mc_pumps_toggle.clicked.connect(on_pumps_toggled)
cd_mc_pumps_toggle.setMinimumWidth(100)
cd_mc_layout.addWidget(cd_mc_pumps_toggle)

cd_mc_layout.setSpacing(6)
cd_mc_layout.setContentsMargins(3,10,3,3)
cd_vbox.addWidget(cd_mc_box)


tabs[2].setLayout(cd_vbox)

# Set up the GUI - Rate testing tab
rate_vbox = QtWidgets.QVBoxLayout()
rate_vbox.setAlignment(QtCore.Qt.AlignTop)

rate_params_box = QtWidgets.QGroupBox(title="Rate testing parameters", flat=False)
format_box_for_parameter(rate_params_box)
rate_params_layout = QtWidgets.QVBoxLayout()
rate_params_box.setLayout(rate_params_layout)
rate_lbound_entry_label, rate_lbound_entry = make_label_entry(rate_params_layout, "Lower bound (V)")
rate_ubound_entry_label, rate_ubound_entry = make_label_entry(rate_params_layout, "Upper bound (V)")
rate_capacity_entry_label, rate_capacity_entry = make_label_entry(rate_params_layout, u"C (µAh)")
rate_crates_entry_label, rate_crates_entry = make_label_entry(rate_params_layout, u"C-rates")
rate_crates_entry.setText("1, 2, 5, 10, 20, 50, 100")
rate_numcycles_entry_label, rate_numcycles_entry = make_label_entry(rate_params_layout, u"Cycles per C-rate")

rate_params_layout.setSpacing(6)
rate_params_layout.setContentsMargins(3,10,3,3)
rate_vbox.addWidget(rate_params_box)

rate_file_box = QtWidgets.QGroupBox(title="Output data filename", flat=False)
format_box_for_parameter(rate_file_box)
rate_file_layout = QtWidgets.QVBoxLayout()
rate_file_box.setLayout(rate_file_layout)
rate_file_choose_layout = QtWidgets.QHBoxLayout()
rate_file_entry = QtWidgets.QLineEdit()
rate_file_choose_layout.addWidget(rate_file_entry)
rate_file_choose_button = QtWidgets.QPushButton("...")
rate_file_choose_button.setFixedWidth(32)
rate_file_choose_button.clicked.connect(lambda: choose_file(rate_file_entry,"Choose where to save the rate testing measurement data"))
rate_file_choose_layout.addWidget(rate_file_choose_button)
rate_file_layout.addLayout(rate_file_choose_layout)

rate_file_layout.setSpacing(6)
rate_file_layout.setContentsMargins(3,10,3,3)
rate_vbox.addWidget(rate_file_box)

rate_start_button = QtWidgets.QPushButton("Start Rate Test")
rate_start_button.clicked.connect(rate_start)
rate_vbox.addWidget(rate_start_button)
rate_stop_button = QtWidgets.QPushButton("Stop Rate Test")
rate_stop_button.clicked.connect(lambda: rate_stop(interrupted=True))
rate_vbox.addWidget(rate_stop_button)

rate_vbox.setSpacing(6)
rate_vbox.setContentsMargins(3,3,3,3)

rate_info_box = QtWidgets.QGroupBox(title="Information", flat=False)
format_box_for_parameter(rate_info_box)
rate_info_layout = QtWidgets.QVBoxLayout()
rate_info_box.setLayout(rate_info_layout)
rate_current_crate_entry_label, rate_current_crate_entry = make_label_entry(rate_info_layout, "Current C-rate")
rate_current_crate_entry.setReadOnly(True)

rate_info_layout.setSpacing(6)
rate_info_layout.setContentsMargins(3,10,3,3)
rate_vbox.addWidget(rate_info_box)

tabs[3].setLayout(rate_vbox)

hbox = QtWidgets.QHBoxLayout()
hbox.addLayout(display_plot_frame)
hbox.addWidget(tab_frame)

vbox = QtWidgets.QVBoxLayout()
statustext = QtWidgets.QPlainTextEdit()
statustext.setFixedHeight(90)
vbox.addLayout(hbox)
vbox.addWidget(statustext)

mainwidget = QtWidgets.QWidget()
win.setCentralWidget(mainwidget)
vbox.setContentsMargins(0,0,0,0)
mainwidget.setLayout(vbox)

def periodic_update(): # A state machine is used to determine which functions need to be called, depending on the current state of the program
    if state == States.Idle_Init:
        idle_init()
    elif state == States.Idle:
        read_potential_current()
        update_live_graph()
        emergency_shutdown()
    elif state == States.Measuring_CV:
        cv_update()
        emergency_shutdown()
    elif state == States.Measuring_CD:
        cd_update()
        emergency_shutdown()
    elif state == States.Measuring_Rate:
        rate_update()
        emergency_shutdown()
    elif state == States.Stationary_Graph:
        read_potential_current()
        emergency_shutdown()
    
    try:
        global time_from_last_arduino_update, arduino_in_endpoint
        if arduino_dev is not None and timeit.default_timer() - time_from_last_arduino_update > 1:
            data = arduino_dev.read(arduino_in_endpoint, 30, 30) # just guessed on the 30, 30 values
            data = ''.join([chr(x) for x in data])
            rpm1 = int(data.split(",")[0])        
            rpm2 = int(data.split(",")[1])
            cd_mc_m1label.setText("Positive Pump Control - {} RPM".format(rpm1))
            cd_mc_m2label.setText("Negative Pump Control - {} RPM".format(rpm2))
            time_from_last_arduino_update = timeit.default_timer()
    except:
        pass

timer = QtCore.QTimer()
timer.timeout.connect(periodic_update)
timer.start(int(1e3*adcread_interval)) # Calls periodic_update() every adcread_interval (as defined in the beginning of this program)
log_message("Program started. Press the \"Connect\" button in the hardware tab to connect to the USB interface.")

refresh_arduino_devices()
refresh_device_selector(hardware_usb_device_dropdown, hardware_usb_vid, hardware_usb_pid, "selected_usb_identifier")
win.show() # Show the main window
base = getattr(sys, "_MEIPASS", os.path.abspath("."))
icon_path = os.path.join(base, "icon", "icon.png")
win.setWindowIcon(QtGui.QIcon(icon_path))
sys.exit(app.exec_()) # Keep the program running by periodically calling the periodic_update() until the GUI window is closed
