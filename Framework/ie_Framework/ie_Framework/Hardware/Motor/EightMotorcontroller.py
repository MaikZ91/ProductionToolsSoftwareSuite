import sys
import time

import serial
import serial.tools.list_ports


class _NullSerial:
    """Dummy Serial-Ersatz, damit das Programm auch ohne COM-Port laeuft."""

    def __init__(self):
        self.is_open = True

    def write(self, data):
        return 0

    def reset_input_buffer(self):
        return None

    def read(self, n):
        return b"\x00" * n

    def close(self):
        self.is_open = False


class MotorController(object):
    """
    Simple wrapper to communicate with a motor controller via a serial port.
    Provides functions for sending position commands, reading status data,
    reading configuration data, setting speed and writing analog values.
    """

    def __init__(self, port_number: int = 0, baud_rate: int = 9600,
                 verbose: bool = True) -> None:
        """
        Initialize the serial connection.

        If port_number == 0, all available COM ports are scanned and the first
        port that responds to a probe command is selected automatically.
        """
        self.verbose = verbose

        if port_number == 0:
            self.ser = self.select_port(baud_rate)
        else:
            try:
                self.ser = serial.Serial(f"COM{port_number}", baud_rate, timeout=2)
            except (serial.SerialException, OSError, PermissionError) as exc:
                # Wenn kein Zugriff auf den Port moeglich ist, Programm trotzdem starten lassen.
                print(f"[WARN] Unable to open COM{port_number}: {exc}")
                self.ser = _NullSerial()

        if not self.ser.is_open:
            print("Unable to open COM port")

        # Number of fixed positions returned by the configuration request.
        # -1 means "unknown" and will be set on first config read.
        self.mxfp = -1

    def __del__(self) -> None:
        """Close the serial port when the object is destroyed."""
        if hasattr(self, "ser") and self.ser and self.ser.is_open:
            self.ser.close()

    def select_port(self, baud_rate: int) -> serial.Serial:
        """
        Scan all COM ports and select the first one that responds to a probe frame.

        This function sends a simple request and checks if any data is returned.
        If yes, the port is considered valid for communication.
        """
        for element in serial.tools.list_ports.comports():
            port = element.device

            if self.verbose:
                print(f"Trying port: {port}")

            try:
                test = serial.Serial(port, baud_rate, timeout=2)
            except (serial.SerialException, OSError, PermissionError) as exc:
                if self.verbose:
                    print(f"  -> skipping {port}: {exc}")
                continue
            test.reset_input_buffer()

            # Basic probe command — the device must return some data
            query = bytearray([0x12, 0xC0])
            test.write(query)

            if len(test.read(18)) > 0:
                if self.verbose:
                    print(f"Port selected: {port}")
                return test

        print("No suitable device found on any COM port. Running without motor connection.")
        return _NullSerial()

    @staticmethod
    def int_to_bytes(value: int, length: int) -> bytearray:
        """
        Convert an integer into a little-endian bytearray of the given length.
        """
        return bytearray((value >> (i * 8)) & 0xFF for i in range(length))
    
    def move_to_pos(self, addr: int, pos: int, ypos: bool = False) -> None:
        """
        Move a motor with the given address to an absolute target position.

        addr : int - Motor address on the bus (controller-specific, set by ini. file with TRIMSCOPE.exe).
        This selects which motor should execute the move.
        pos : int - Target position in motor steps (or microsteps), as expected by the controller.
        ypos : int or bool -  Optional flag for your own usage (e.g. indicate Y-direction moves).

        # Build command frame: [address, command_code(0x00), pos as 4 bytes]
        """
        data = bytearray([addr, 0x00]) + self.int_to_bytes(pos, 4)

        # Optional debug output (suppressed for Y moves if ypos=True)
        if not ypos and self.verbose:
            print(f"Move motor {addr} to position {pos}")

        # Send command to the motor controller via the serial port
        self.ser.write(data)


    def current_pos(self, addr: int) -> int:
        """Query and return the current position of a motor in steps.
        Parameters
        addr : int- Motor address on the bus.
        Return only the current position from the status frame."""
        return int.from_bytes(self.motor_status(addr)[1:5], "little")
    
    def set_analog_output(self, address: int, platform: int, value: int) -> None:
        """
        Set the controller’s analog output channel.
        Some controllers provide analog output ports (e.g. DAC channels).
        This command writes a 16-bit value to one of these outputs.

        address  : selects the analog output channel  
        platform : optional sub-address / module selector  
        value    : analog value (lower 16 bits are used)
        """
        value &= 0x0FFF
        frame = bytes([address, platform, value & 0xFF, (value >> 8) & 0x0F])
        self.ser.write(frame)


    def motor_status(self, addr: int) -> bytes:
        """
        Request the status of the motor at the given address.
        The device responds with a fixed-length status frame.
        """
        self.ser.reset_input_buffer()
        self.ser.write(bytearray([addr, 0xC1]))
        return self.ser.read(9)

    @staticmethod
    def analyse_status_flag(status: bytes) -> dict:
        """
        Interpret the first status byte as a bitfield and return a dict
        with labeled bits. The meaning of each bit is defined by this code only.
        """
        flag = ''.join(format(b, '08b') for b in status[:1])
        return {
            "enbl": flag[0],
            "busy": flag[1],
            "ref":  flag[2],
            "err":  flag[3],
            "nr":   flag[4],
            "SWp":  flag[6],
            "SWm":  flag[7],
        }

    def motor_moving(self, status: bytes) -> bool:
        """Return True if the motor is marked as busy."""
        return self.analyse_status_flag(status)["busy"] == "1"

    def reached_pos(self, addr) -> bool:
        """
        Compare current position and target position values
        extracted from the returned status frame.
        """
        status = self.motor_status(addr)
        current_position = int.from_bytes(status[1:5], "little")
        target_position = int.from_bytes(status[5:9], "little")
        return current_position == target_position


    def set_motor_speed(self, addr: int, vstp: int) -> None:
        """
        Send a motor speed configuration value to the device.
        Command format: [address, speed_code, speed(bytes)].
        """
        frame = bytearray([addr, 0xC6]) + vstp.to_bytes(4, "little")
        self.ser.write(frame)

    def motor_config(self, addr: int) -> bytes:
        """
        Request and return a configuration frame for the motor.
        The length of the returned data depends on the device and is partially
        determined by the first response (mxfp).
        """
        self.ser.reset_input_buffer()
        frame = bytearray([addr, 0xC0])
        self.ser.write(frame)

        if self.mxfp == -1:
            header = self.ser.read(10)
            if not header:
                raise RuntimeError("No configuration header received.")
            self.mxfp = header[0]
            self.ser.read((self.mxfp + 1) * 4)
            self.ser.write(frame)

        return self.ser.read(self.mxfp * 4 + 14)

    def motor_fixed_positions(self, config: bytes) -> list[int]:
        """
        Extract a list of fixed positions from the configuration data.
        Each position is encoded as 4 consecutive bytes.
        """
        positions = []
        for i in range(self.mxfp + 1):
            start = 4 * i + 1
            end = start + 4
            positions.append(int.from_bytes(config[start:end], "little"))
        return positions
    
    def reference_motor(self, addr: int) -> None:
        """
        Sends a reference command to the motor controller.
        """
        query = bytearray([addr, 0xCA])
        self.ser.write(query)

    def move_to_fix_pos(self, addr: int, fixed_pos: int) -> None:
        """
        Moves the motor to a predefined fixed position.
        """
        query = bytearray([addr, fixed_pos + 128])
        self.ser.write(query)
        
    def move_stage_cycle():
        """
        Perform a full movement cycle involving three motor axes.

        Note:
        The meaning of the motor addresses (e.g., X/Y/Z) is not fixed.
        Their axis assignment depends entirely on the system's INI/config file.
        This function therefore treats them as generic motor channels.

        Sequence:
        1. Move motor at address 18 → position 97000
        2. Move motor at address 19 → position 320000
        3. Move motor at address 20 → position 0
        (These values represent one defined extended stage position.)

        4. Hold this state for 20 seconds

        5. Move all three motors back to their base positions:
            - Address 18 → 97000
            - Address 19 → 0
            - Address 20 → 0

        In summary:
        This routine performs a complete “move out → hold → move back” cycle
        for the three configured stage axes, independent of their physical meaning.
        """
        controller = MotorController(port_number=6) 
        # Move all configured stage axes to their extended positions
        controller.move_to_pos(18, 97000)
        controller.move_to_pos(19, 320000)
        controller.move_to_pos(20, 0)

        # Hold the extended position
        time.sleep(20)

        # Return all axes to their defined base positions
        controller.move_to_pos(18, 97000)
        controller.move_to_pos(19, 0)
        controller.move_to_pos(20, 0)


def main() -> int:
    """Basic usage example."""
    # Create a motor controller instance and open serial connection on COM6.
    controller = MotorController(port_number=6) 
    # Move motor with address 18 to absolute position 97000 steps. 
    controller.move_to_pos(18, 97000)
    # Request and read the current position of motor 18 (returned in steps).
    pos = controller.current_pos(18)


if __name__ == "__main__":
    sys.exit(main())


def startposition(
    controller: MotorController,
    addr_a: int,
    addr_b: int,
    addr_c: int,
    pos_a: int,
    pos_b: int,
    pos_c: int,
    *,
    wait: bool = True,
) -> bool:
    """Move three axes to start positions defined by the caller."""
    controller.move_to_pos(addr_a, pos_a)
    controller.move_to_pos(addr_b, pos_b)
    controller.move_to_pos(addr_c, pos_c)
    if wait:
        while controller.current_pos(addr_b) != pos_b or controller.current_pos(addr_c) != pos_c:
            pass
    return True


def endposition(
    controller: MotorController,
    addr_a: int,
    addr_b: int,
    addr_c: int,
    pos_a: int,
    pos_b: int,
    pos_c: int,
) -> None:
    """Move three axes to end positions defined by the caller."""
    controller.move_to_pos(addr_a, pos_a)
    controller.move_to_pos(addr_b, pos_b)
    controller.move_to_pos(addr_c, pos_c)

