import cv2


class DinoLiteController:
    """Wrapper um eine DinoLite-USB-Kamera."""

    def __init__(self, device_index: int = 0):
        self.cap = cv2.VideoCapture(device_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2592)    # max width
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1944)   # max height
        self.running = False

        if not self.cap.isOpened():
            raise ValueError("Kamera konnte nicht geöffnet werden")

    def capture_image(self):
        ret, frame = self.cap.read()
        if not ret:
            raise ValueError("Bild konnte nicht aufgenommen werden")
        return frame

    def show_live_feed(self):
        self.running = True
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                break

            w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            frame_res = cv2.resize(frame, (int(w/2), int(h/2)))
            cv2.imshow("Live Feed", frame_res)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                self.running = False

        cv2.destroyAllWindows()

    def release(self):
        self.cap.release()


class DummyDinoLite:
    """Fallback-Kamera, die klar signalisiert, dass keine Hardware vorhanden ist."""

    def __init__(self, exc=None):
        self.connected = False
        self._exc = exc

    def capture_image(self):
        raise RuntimeError(f"Kamera nicht verfügbar: {self._exc or 'unbekannter Fehler'}")

    def show_live_feed(self):
        print("[WARN] Kamera nicht verfügbar; Live-Feed deaktiviert.")

    def release(self):
        pass
