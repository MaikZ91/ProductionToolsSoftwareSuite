import pco
from pco import defs


class PcoCameraBackend:
    """Minimal PCO camera backend for GUI usage."""

    def __init__(self):
        self._cam = None
        self._recording = False

    def start(self):
        if self._cam is None:
            self._cam = pco.Camera()
            try:
                self._cam.configuration = {
                    "trigger": "auto sequence",
                    "acquire": "auto",
                }
            except Exception:
                pass
        if not self._recording:
            try:
                self._cam.record(number_of_images=10, mode="ring buffer")
            except Exception:
                self._cam.record(number_of_images=5, mode="sequence non blocking")
            self._recording = True

    def stop(self):
        if self._cam is None:
            return
        try:
            if self._recording:
                self._cam.stop()
        finally:
            self._recording = False
            try:
                self._cam.close()
            finally:
                self._cam = None

    def get_frame(self):
        if self._cam is None:
            return None
        image, _meta = self._cam.image(defs.PCO_RECORDER_LATEST_IMAGE)
        return image

    def get_exposure_s(self):
        if self._cam is None:
            return None
        return self._cam.exposure_time

    def set_exposure_ms(self, exposure_ms):
        if self._cam is None:
            return
        self._cam.exposure_time = float(exposure_ms) / 1000.0

    def get_exposure_limits_s(self):
        if self._cam is None:
            return None
        try:
            desc = self._cam.description
        except Exception:
            return None
        min_s = desc.get("min exposure time")
        max_s = desc.get("max exposure time")
        if min_s is None or max_s is None:
            return None
        return float(min_s), float(max_s)

    def get_recorded_count(self):
        if self._cam is None:
            return None
        try:
            return int(self._cam.recorded_image_count)
        except Exception:
            return None
