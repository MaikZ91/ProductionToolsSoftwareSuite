import os
import time
import sys
import subprocess

_PICAM_IMPORT_ERROR = None
if sys.platform.startswith("win"):
    Picamera2 = None
    Preview = None
    gp = None
else:
    try:
        from picamera2 import Picamera2, Preview
        import RPi.GPIO as gp
    except Exception as exc:
        Picamera2 = None
        Preview = None
        gp = None
        _PICAM_IMPORT_ERROR = exc


class Picamera:
    def __init__(self):
        if Picamera2 is None or gp is None:
            detail = f": {_PICAM_IMPORT_ERROR}" if _PICAM_IMPORT_ERROR else ""
            raise RuntimeError(f"Picamera2 is not available on this system{detail}")
        self.picam2 = Picamera2()
        try:
            self.picam2.configure(self.picam2.create_preview_configuration({'size': (1000, 1000)}))
        except Exception as exc:
            if "Device or resource busy" in str(exc):
                subprocess.run(
                    ["pkill", "-f", "libcamera"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.2)
                try:
                    self.picam2.configure(self.picam2.create_preview_configuration({'size': (1000, 1000)}))
                except Exception:
                    pass
            try:
                self.picam2.close()
            except Exception:
                pass
            raise
        self.current_camera = None
        self.count = 0
        self.adapter_info = {
            "A": {"i2c_cmd": "i2cset -y 1 0x70 0x00 0x04", "gpio_sta": [0, 0, 1]},
            "B": {"i2c_cmd": "i2cset -y 1 0x70 0x00 0x05", "gpio_sta": [1, 0, 1]},
            "C": {"i2c_cmd": "i2cset -y 1 0x70 0x00 0x06", "gpio_sta": [0, 1, 0]},
            "D": {"i2c_cmd": "i2cset -y 1 0x70 0x00 0x07", "gpio_sta": [1, 1, 0]},
        }
        self.camera_dict = {
            'Y': {"camera": "A", "controls": {"AeEnable": False, "AnalogueGain": 1.0, "AwbEnable": False, "ColourGains": (1.0, 1.0), "ExposureTime": 1}},
            'Y1': {"camera": "D", "controls": {"AeEnable": False, "AnalogueGain": 1.0, "AwbEnable": False, "ColourGains": (1.0, 1.0), "ExposureTime": 1}},
            'Z': {"camera": "B", "controls": {"AeEnable": False, "AnalogueGain": 1.0, "AwbEnable": False, "ColourGains": (1.0, 1.0), "ExposureTime": 1}},
            'Z1': {"camera": "C", "controls": {"AeEnable": False, "AnalogueGain": 1.0, "AwbEnable": False, "ColourGains": (1.0, 1.0), "ExposureTime": 1000}},
        }
        self.initialize_gpio()

    def initialize_gpio(self):
        gp.setwarnings(False)
        gp.setmode(gp.BOARD)
        gp.setup(7, gp.OUT)
        gp.setup(11, gp.OUT)
        gp.setup(12, gp.OUT)

    def select_camera(self, index):
        channel_info = self.adapter_info.get(index)
        gpio_sta = channel_info["gpio_sta"]
        gp.output(7, gpio_sta[0])
        gp.output(11, gpio_sta[1])
        gp.output(12, gpio_sta[2])
        os.system(channel_info["i2c_cmd"])

    def _open_camera(self, axis, preview=False):
        camera_info = self.camera_dict[axis]
        if self.current_camera:
            self.picam2.stop()
        self.select_camera(camera_info["camera"])
        self.picam2.set_controls(camera_info["controls"])
        if preview:
            self.picam2.start_preview(Preview.QT, x=100, y=200, width=800, height=600)
        self.picam2.start()
        self.current_camera = axis

    def capture_pair(self, camera1, camera2):
        self._open_camera(camera1)
        time.sleep(0.2)
        np_img = self.picam2.capture_array()
        self.picam2.stop()
        self._open_camera(camera2)
        time.sleep(0.2)
        np_img2 = self.picam2.capture_array()
        self.picam2.stop()
        self.count += 1
        return np_img, np_img2

    def capture_frame(self, axis):
        self._open_camera(axis)
        time.sleep(0.2)
        np_img = self.picam2.capture_array()
        self.picam2.stop()
        self.count += 1
        return np_img

    def test_camera(self, axis):
        self._open_camera(axis, preview=False)


__all__ = ["Picamera"]
