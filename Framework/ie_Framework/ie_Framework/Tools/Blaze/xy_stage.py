import os
import time
import threading

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ie_Framework.Algorithm.laser_spot_detection import LaserSpotDetector
from ie_Framework.Hardware.Camera.PicameraController import Picamera
from ie_Framework.Hardware.Motor.EightMotorcontroller import MotorController, endposition, startposition

os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)


class StageTestBackend:
    def __init__(self, stage_ctrl=None, picamera=None):
        self.stage_ctrl = stage_ctrl or MotorController(port_number=0, baud_rate=9600, verbose=False)
        self._camera_error = None
        self.picamera = picamera
        self._cam_lock = threading.Lock()
        self._data_lock = threading.Lock()
        self.motorsteps = 0
        self.pos = []
        self.x_coord, self.y_coord, self.x_coord2, self.y_coord2 = [], [], [], []
        self.max_steps = 320000
        self.seriennummer = 0
        self.addr_a = 18
        self.addr_b = 19
        self.addr_c = 20
        self.start_pos_a = 97000
        self.start_pos_b = 0
        self.start_pos_c = 0
        self.end_pos_a = 97000
        self.end_pos_b = 321000
        self.end_pos_c = 80000

    @property
    def camera_error(self):
        return self._camera_error

    def _ensure_camera(self):
        if self.picamera is None:
            try:
                self.picamera = Picamera()
                self._camera_error = None
            except Exception as exc:
                self._camera_error = str(exc)
                time.sleep(0.5)
                try:
                    self.picamera = Picamera()
                    self._camera_error = None
                except Exception as exc_retry:
                    self._camera_error = str(exc_retry)
        if self.picamera is None:
            detail = f": {self._camera_error}" if self._camera_error else ""
            raise RuntimeError(f"Kamera nicht verfuegbar{detail}")
        return self.picamera

    def set_serial_number(self, seriennummer):
        self.seriennummer = seriennummer

    def clear_list(self):
        with self._data_lock:
            self.pos.clear()
            self.y_coord.clear()
            self.y_coord2.clear()
            self.x_coord.clear()
            self.x_coord2.clear()

    def append_to_coordinates(self, x_coord, y_coord, mean_x, mean_y):
        x_coord.append(mean_x)
        y_coord.append(mean_y)

    def get_live_snapshot(self):
        with self._data_lock:
            return {
                "pos": list(self.pos),
                "x_coord": list(self.x_coord),
                "y_coord": list(self.y_coord),
                "x_coord2": list(self.x_coord2),
                "y_coord2": list(self.y_coord2),
            }

    def capture_frame(self, axis):
        if self.picamera is None:
            return None
        if not self._cam_lock.acquire(False):
            return None
        try:
            return self.picamera.capture_frame(axis)
        except Exception:
            return None
        finally:
            self._cam_lock.release()

    def process_image(self, motor, axis, steps, sleep_s, progress_cb=None):
        cam = self._ensure_camera()
        if axis == 'Y':
            with self._cam_lock:
                np_img, np_img2 = cam.capture_pair('Y1', 'Y')
        if axis == 'Z':
            with self._cam_lock:
                np_img, np_img2 = cam.capture_pair('Z', 'Z1')

        gray_img = np_img[..., 1]
        gray_img2 = np_img2[..., 1]

        x, y, _ = LaserSpotDetector.detect_laser_spot_otsu(gray_img)
        x2, y2, _ = LaserSpotDetector.detect_laser_spot_otsu(gray_img2)

        with self._data_lock:
            self.append_to_coordinates(self.x_coord, self.y_coord, x, y)
            self.append_to_coordinates(self.x_coord2, self.y_coord2, x2, y2)
            self.pos.append(self.stage_ctrl.current_pos(motor))

        cv2.circle(np_img, (int(x), int(y)), 2, (255, 255, 255), -1)
        cv2.circle(np_img2, (int(x2), int(y2)), 2, (255, 255, 255), -1)

        if axis == 'Y':
            self.motorsteps += steps
        if axis == 'Z':
            self.motorsteps += 500

        self.stage_ctrl.move_to_pos(motor, self.motorsteps)
        if progress_cb:
            progress_cb(self.motorsteps, self.max_steps)
        time.sleep(sleep_s)

    def process_image_wdh(self, motor, axis, steps, sleep_s):
        cam = self._ensure_camera()
        if axis == 'Y':
            with self._cam_lock:
                np_img, np_img2 = cam.capture_pair('Y1', 'Y')
        if axis == 'Z':
            with self._cam_lock:
                np_img, np_img2 = cam.capture_pair('Z', 'Z1')

        gray_img = np_img[..., 1]
        gray_img2 = np_img2[..., 1]

        x, y, _ = LaserSpotDetector.detect_laser_spot_otsu(gray_img)
        x2, y2, _ = LaserSpotDetector.detect_laser_spot_otsu(gray_img2)

        with self._data_lock:
            self.append_to_coordinates(self.x_coord, self.y_coord, x, y)
            self.append_to_coordinates(self.x_coord2, self.y_coord2, x2, y2)
            self.pos.append(self.stage_ctrl.current_pos(motor))

    def startposition(self, wait=True):
        return startposition(
            self.stage_ctrl,
            self.addr_a,
            self.addr_b,
            self.addr_c,
            self.start_pos_a,
            self.start_pos_b,
            self.start_pos_c,
            wait=wait,
        )

    def endposition(self):
        endposition(
            self.stage_ctrl,
            self.addr_a,
            self.addr_b,
            self.addr_c,
            self.end_pos_a,
            self.end_pos_b,
            self.end_pos_c,
        )

    def position_y(self):
        self.stage_ctrl.move_to_pos(self.addr_a, self.start_pos_a)
        self.stage_ctrl.move_to_pos(self.addr_b, 0)
        self.stage_ctrl.move_to_pos(self.addr_c, 0)

    def test_camera(self, axis):
        cam = self._ensure_camera()
        with self._cam_lock:
            cam.test_camera(axis)

    def showResults(self, axis):
        x1, x_fit1, dis_x1, poly_x1, x1_derivative, x1_max_slope = self.berechne_ausgleichsgerade(self.pos, self.x_coord)
        x2, x_fit2, dis_x2, poly_x2, x2_derivative, x2_max_slope = self.berechne_ausgleichsgerade(self.pos, self.x_coord2)
        y1, y_fit1, dis_y1, poly_y1, y1_derivative, y1_max_slope = self.berechne_ausgleichsgerade(self.pos, self.y_coord)
        y2, y_fit2, dis_y2, poly_y2, y2_derivative, y2_max_slope = self.berechne_ausgleichsgerade(self.pos, self.y_coord2)

        sample_pos_x, cam1_x, cam2_x, fov_x = self.calculate_slide_of_view(x2, x1)
        sample_pos_y, cam1_y, cam2_y, fov_y = self.calculate_slide_of_view(y2, y1)

        fig, (ax1, ax2, ax3, ax4) = plt.subplots(1, 4, figsize=(16, 6))

        pos = np.array(self.pos) * (48000 / 321000)

        ax1.plot(pos[1:-1], fov_x, label="FOV X", color='black', linewidth=1)
        ax1.set_title("Field of View X")
        ax1.set_xlabel("Position[Âµm]")
        ax1.set_ylabel("X-Werte")
        ax1.legend()

        ax2.plot(pos, sample_pos_x, label="Sample Pos X", color='black', linewidth=1)
        ax2.set_title("Positions in X Direction")
        ax2.set_xlabel("Position[Âµm]")
        ax2.set_ylabel("X-Werte")
        ax2.legend()

        ax3.plot(pos[1:-1], fov_y, label="FOV Z", color='black', linewidth=1)
        ax3.set_title("Field of View Z")
        ax3.set_xlabel("Position[Âµm]")
        ax3.set_ylabel("Z-Werte")
        ax3.legend()

        ax4.plot(pos, sample_pos_y, label="Sample Pos Z", color='black', linewidth=1)
        ax4.set_title("Positions in Z Direction")
        ax4.set_xlabel("Position[Âµm]")
        ax4.set_ylabel("Z-Werte")
        ax4.legend()

        plt.tight_layout()
        plt.savefig(f"/home/pi/stage_test/auswertung_{self.seriennummer}_xy_directions.png")
        df1 = pd.DataFrame({'Position': pos, 'X- Auslenkung_Cam1': x1, 'X-Auslenkung_Cam2': x2, 'Z- Auslenkung_Cam1': y1, 'Z-Auslenkung_Cam2': y2, 'SamplePosX': sample_pos_x, 'SamplePosZ': sample_pos_y})
        df2 = pd.DataFrame({'Position': pos[1:-1], 'Field of View X': fov_x, 'Field of View Y': fov_y})
        df1.to_excel(f"/home/pi/stage_test/Rohdaten_{self.seriennummer}.xlsx")
        df2.to_excel(f"/home/pi/stage_test/FOV_{self.seriennummer}.xlsx")
        df3 = pd.DataFrame({'Position': pos, 'X- Koordinate_Cam1': self.x_coord, 'Y-Koordinate_Cam1': self.y_coord, 'X- Koordinate_Cam2': self.x_coord2, 'Y-Koordinate_Cam2': self.y_coord2})
        df3.to_excel(f"/home/pi/stage_test/RAW_{self.seriennummer}.xlsx")
        plt.show()

    def saveRawResults(self):
        df = pd.DataFrame({'Position': self.pos, 'X- Koordinate_Cam1': self.x_coord, 'Y-Koordinate_Cam1': self.y_coord, 'X- Koordinate_Cam2': self.x_coord2, 'Y-Koordinate_Cam2': self.y_coord2})
        df.to_excel(f"/home/pi/stage_test/Rohdaten_Koordinaten.xlsx")

    def berechne_ausgleichsgerade(self, x_werte, y_werte):
        y_werte = np.array(y_werte) * 1.12

        steigung, _ = np.polyfit(x_werte, y_werte, 1)
        y_slope_korrigiert = y_werte - np.array(self.pos) * steigung

        y_baseline = y_slope_korrigiert - np.mean(y_slope_korrigiert)

        coefficients = np.polyfit(x_werte, y_baseline, 10)
        poly_function = np.poly1d(coefficients)
        x_fine = np.linspace(min(x_werte), max(x_werte), 100)
        y_fit_grob = poly_function(x_werte)
        y_fit = poly_function(x_fine)

        poly_derivative = poly_function.deriv()

        y_derivative = poly_derivative(x_fine)
        max_slope = np.max(np.abs(y_derivative))

        delta_y = abs(y_baseline - y_fit_grob)
        delta_poly = abs(max(y_fit) - min(y_fit))

        return y_baseline, y_fit, delta_y, delta_poly, y_derivative, max_slope

    def calculate_slide_of_view(self, cam_1, cam_2):
        slope = (cam_1 - cam_2) / (60 - 0)

        sample_pos = slope * 52
        cam1 = slope * 44 + cam_2
        cam2 = slope * 0 + cam_2

        fov = []
        for i in range(len(sample_pos) - 2):
            three_values = sample_pos[i:i+3]
            delta = max(three_values) - min(three_values)
            fov.append(delta)

        return sample_pos, cam1, cam2, fov

    def measure_axis(self, axis, steps, cycles, sleep_s=1, progress_cb=None):
        for _ in range(int(cycles)):
            if self.startposition():
                limit = 320000 if axis == 'Y' else 80000
                motor_addr = 19 if axis == 'Y' else 20
                while self.stage_ctrl.current_pos(motor_addr) < limit:
                    self.process_image(motor_addr, axis, int(steps), sleep_s, progress_cb=progress_cb)
                self.motorsteps = 0
        self.showResults(axis)

    def measure_all_axis(self, steps, cycles, sleep_s=1, progress_cb=None):
        self.measure_axis('Y', steps, cycles, sleep_s, progress_cb=progress_cb)
        self.measure_axis('Z', steps, cycles, sleep_s, progress_cb=progress_cb)

    def test_y(self):
        self.stage_ctrl.move_to_pos(self.addr_a, self.start_pos_a)
        self.stage_ctrl.move_to_pos(self.addr_b, 320000)
        self.stage_ctrl.move_to_pos(self.addr_c, 0)
        time.sleep(20)
        self.stage_ctrl.move_to_pos(self.addr_a, self.start_pos_a)
        self.stage_ctrl.move_to_pos(self.addr_b, 0)
        self.stage_ctrl.move_to_pos(self.addr_c, 0)

    def test_z(self):
        self.stage_ctrl.move_to_pos(self.addr_a, self.start_pos_a)
        self.stage_ctrl.move_to_pos(self.addr_b, 0)
        self.stage_ctrl.move_to_pos(self.addr_c, 82000)
        time.sleep(10)
        self.stage_ctrl.move_to_pos(self.addr_a, self.start_pos_a)
        self.stage_ctrl.move_to_pos(self.addr_b, 0)
        self.stage_ctrl.move_to_pos(self.addr_c, 0)


__all__ = ["StageTestBackend"]
