from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import cv2
    import mediapipe as mp
    import numpy as np
    import pyautogui

    cv2.setUseOptimized(True)
except ModuleNotFoundError as exc:
    missing = exc.name or "package"
    message = (
        "\nRequired Python package not found: "
        f"{missing}\n\n"
        "This project requires packages to be installed in a virtual environment (.venv).\n"
        "Please run `install.bat` first, then start with `run.bat`.\n"
    )
    raise SystemExit(message) from exc


# MediaPipe el noktalarini okunabilir isimlerle kullanmak icin sabitler.
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_TIP = 8
INDEX_PIP = 6
MIDDLE_TIP = 12
MIDDLE_PIP = 10
RING_TIP = 16
RING_PIP = 14
PINKY_MCP = 17
PINKY_TIP = 20
PINKY_PIP = 18

# OpenCV penceresinden kontrol tuslari.
WINDOW_TITLE = "Vision-Based PC Control"
EXIT_KEYS = {ord("q"), 27}
PAUSE_KEY = ord("p")
PID_FILE = Path(__file__).with_name("el_kontrolu.pid")

# Fare hareketlerinde pyautogui'nin varsayilan beklemelerini kapatir.
pyautogui.FAILSAFE = True
pyautogui.MINIMUM_DURATION = 0.0
pyautogui.MINIMUM_SLEEP = 0.0
pyautogui.PAUSE = 0.0


@dataclass(frozen=True)
class ControlConfig:
    """Uygulamanin performans ve hareket hassasiyeti ayarlari."""

    camera_index: int = 0
    camera_width: int = 640
    camera_height: int = 360
    camera_fps: int = 60
    # Dusuk complexity ve esnek esikler daha hizli ilk algilama ve daha dusuk gecikme saglar.
    detection_confidence: float = 0.38
    tracking_confidence: float = 0.42
    model_complexity: int = 0
    draw_hand_lines: bool = False
    # Imlec kucuk hareketlerde yumusak, buyuk hareketlerde hizli tepki verir.
    movement_smoothing: float = 0.28
    fast_movement_smoothing: float = 0.70
    fast_movement_distance_px: int = 320
    mouse_move_duration: float = 0.0
    # Tiklama tekrarini parmak acilip kapanmasina baglayarak hizli ama kontrollu algilar.
    click_cooldown_seconds: float = 0.20
    pause_cooldown_seconds: float = 0.85
    pinch_threshold: float = 0.045
    pinch_scale: float = 0.30
    pinch_release_multiplier: float = 1.45
    scroll_threshold: float = 0.020
    scroll_amount: int = 130
    # Daha genis kamera alani kullanmak imlec hassasiyetini azaltir.
    frame_margin: float = 0.02
    cursor_deadzone_px: int = 3
    max_cursor_step_px: int = 190
    # El uzun sure algilanmazsa MediaPipe nesnesini yeniden baslat.
    no_hand_reset_seconds: float = 1.4


class CameraStream:
    """Kamerayi ayri is parcaciginda okuyarak kare birikmesini azaltir."""

    def __init__(self, config: ControlConfig) -> None:
        # Windows'ta bazi kameralarda backend uyumsuzluklari olabiliyor.
        # DSHOW -> MSMF -> ANY sirasi ile acmayi deneriz.
        self._cap = self._open_camera(config.camera_index)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.camera_width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.camera_height)
        self._cap.set(cv2.CAP_PROP_FPS, config.camera_fps)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Bazi webcam'lerde MJPG daha stabil/akici olabiliyor; desteklenmiyorsa sorun degil.
        try:
            self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass

        # Son basarili kamera karesi ve okuma durumunu thread-safe saklar.
        self._frame = None
        self._ok = False
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)

    def _open_camera(self, camera_index: int) -> Any:
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
            cap = cv2.VideoCapture(camera_index, backend)
            if cap.isOpened():
                return cap
        # Son deneme: backend belirtmeden.
        return cv2.VideoCapture(camera_index)

    def start(self) -> CameraStream:
        """Kamera okuma is parcacigini baslatir."""
        self._thread.start()
        return self

    def _read_loop(self) -> None:
        """Kameradan surekli son kareyi alir; eski kareleri kuyrukta tutmaz."""
        while not self._stopped.is_set():
            ok, frame = self._cap.read()
            with self._lock:
                self._ok = ok
                if ok:
                    self._frame = frame

            # Kamera okumasi basarisizsa CPU'yu bosuna yormamak icin kisa bekleme.
            if not ok:
                time.sleep(0.005)

    def read(self) -> tuple[bool, Any]:
        """Ana dongunun kullanacagi en yeni kamera karesini dondurur."""
        with self._lock:
            if self._frame is None:
                return False, None
            return self._ok, self._frame

    def is_opened(self) -> bool:
        """Kameranin acilip acilmadigini bildirir."""
        return self._cap.isOpened()

    def release(self) -> None:
        """Thread'i durdurur ve kamera kaynagini serbest birakir."""
        self._stopped.set()
        self._thread.join(timeout=0.8)
        self._cap.release()


class GestureMouse:
    """El isaretlerini fare hareketlerine cevirir."""

    def __init__(self, config: ControlConfig) -> None:
        self._config = config
        self._screen_w, self._screen_h = pyautogui.size()
        self._last_x = self._screen_w / 2
        self._last_y = self._screen_h / 2
        self._last_click_at = 0.0
        self._last_pause_toggle_at = 0.0
        self._paused = False
        self._prev_scroll_y: float | None = None
        self._active_pinch: str | None = None

    @staticmethod
    def _distance(landmarks: Any, first: int, second: int) -> float:
        """Iki el noktasi arasindaki normalize mesafeyi hesaplar."""
        ax, ay = landmarks[first].x, landmarks[first].y
        bx, by = landmarks[second].x, landmarks[second].y
        return math.hypot(ax - bx, ay - by)

    @staticmethod
    def _is_finger_up(landmarks: Any, tip: int, pip: int) -> bool:
        """Parmak ucunun eklemden yukarida olmasini acik parmak sayar."""
        return landmarks[tip].y < landmarks[pip].y

    def reset_scroll(self) -> None:
        """El kayboldugunda eski kaydirma referansini temizler."""
        self._prev_scroll_y = None

    def reset_gestures(self) -> None:
        """El kayboldugunda gecici hareket durumlarini temizler."""
        self.reset_scroll()
        self._active_pinch = None

    def is_paused(self) -> bool:
        """Kontrolun duraklatilip duraklatilmadigini bildirir."""
        return self._paused

    def toggle_pause(self) -> str:
        """Kontrolu klavye veya yumruk hareketiyle duraklatir/devam ettirir."""
        self._paused = not self._paused
        self._last_pause_toggle_at = time.monotonic()
        self.reset_gestures()
        return "Paused" if self._paused else "Resumed"

    def _map_to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Kamera koordinatini ekran koordinatina yumusatarak cevirir."""
        margin = self._config.frame_margin
        usable_area = 1 - (margin * 2)

        # Kenarlardaki titremeyi azaltmak icin aktif kamera alani kirpilir.
        normalized_x = np.clip((x - margin) / usable_area, 0, 1)
        normalized_y = np.clip((y - margin) / usable_area, 0, 1)

        # Kamera aynalandigi icin X ekseni terslenir.
        target_x = self._screen_w * (1 - normalized_x)
        target_y = self._screen_h * normalized_y

        target_distance = math.hypot(target_x - self._last_x, target_y - self._last_y)

        # Kucuk parmak titremelerini imlece yansitma.
        if target_distance < self._config.cursor_deadzone_px:
            return int(self._last_x), int(self._last_y)

        # Adaptif yumusatma: kucuk hareketlerde sakin, buyuk hareketlerde ele yetisen imlec.
        response = min(target_distance / self._config.fast_movement_distance_px, 1.0)
        smoothing = self._config.movement_smoothing + (
            (self._config.fast_movement_smoothing - self._config.movement_smoothing) * response
        )
        delta_x = (target_x - self._last_x) * smoothing
        delta_y = (target_y - self._last_y) * smoothing
        step_distance = math.hypot(delta_x, delta_y)

        # El bir anda siçrasa bile imlec kontrollu hizla yetissin.
        if step_distance > self._config.max_cursor_step_px:
            scale = self._config.max_cursor_step_px / step_distance
            delta_x *= scale
            delta_y *= scale

        self._last_x += delta_x
        self._last_y += delta_y
        return int(self._last_x), int(self._last_y)

    def _toggle_pause_if_fist(self, landmarks: Any) -> None:
        """Tum parmaklar kapaliysa kontrolu duraklatir veya devam ettirir."""
        fingers_down = [
            not self._is_finger_up(landmarks, INDEX_TIP, INDEX_PIP),
            not self._is_finger_up(landmarks, MIDDLE_TIP, MIDDLE_PIP),
            not self._is_finger_up(landmarks, RING_TIP, RING_PIP),
            not self._is_finger_up(landmarks, PINKY_TIP, PINKY_PIP),
        ]

        now = time.monotonic()
        cooldown_done = now - self._last_pause_toggle_at > self._config.pause_cooldown_seconds
        if all(fingers_down) and cooldown_done:
            self.toggle_pause()

    def _handle_clicks(self, landmarks: Any) -> str | None:
        """Basparmak sikistirma hareketlerini tiklama komutlarina cevirir."""
        now = time.monotonic()
        distances = {
            "left": self._distance(landmarks, THUMB_TIP, INDEX_TIP),
            "right": self._distance(landmarks, THUMB_TIP, MIDDLE_TIP),
            "double": self._distance(landmarks, THUMB_TIP, RING_TIP),
        }
        hand_span = max(self._distance(landmarks, INDEX_MCP, PINKY_MCP), 0.01)
        pinch_limit = max(self._config.pinch_threshold, hand_span * self._config.pinch_scale)
        release_limit = pinch_limit * self._config.pinch_release_multiplier

        if self._active_pinch is not None:
            if distances[self._active_pinch] > release_limit:
                self._active_pinch = None
            return None

        pinch_name, pinch_distance = min(distances.items(), key=lambda item: item[1])
        if pinch_distance >= pinch_limit:
            return None

        if now - self._last_click_at < self._config.click_cooldown_seconds:
            self._active_pinch = pinch_name
            return None

        self._active_pinch = pinch_name
        if pinch_name == "left":
            pyautogui.click(button="left")
            self._last_click_at = now
            return "Left Click"

        if pinch_name == "right":
            pyautogui.click(button="right")
            self._last_click_at = now
            return "Right Click"

        if pinch_name == "double":
            pyautogui.doubleClick(button="left")
            self._last_click_at = now
            return "Double Click"

        return None

    def _handle_scroll(self, landmarks: Any) -> str | None:
        """Isaret ve orta parmak acikken elin yukari/asagi hareketini kaydirir."""
        index_up = self._is_finger_up(landmarks, INDEX_TIP, INDEX_PIP)
        middle_up = self._is_finger_up(landmarks, MIDDLE_TIP, MIDDLE_PIP)
        ring_up = self._is_finger_up(landmarks, RING_TIP, RING_PIP)
        pinky_up = self._is_finger_up(landmarks, PINKY_TIP, PINKY_PIP)

        if not (index_up and middle_up and not ring_up and not pinky_up):
            self.reset_scroll()
            return None

        current_y = (landmarks[INDEX_TIP].y + landmarks[MIDDLE_TIP].y) / 2
        if self._prev_scroll_y is None:
            self._prev_scroll_y = current_y
            return "Scroll Ready"

        delta_y = current_y - self._prev_scroll_y
        if abs(delta_y) < self._config.scroll_threshold:
            return "Scroll Ready"

        scroll_direction = -1 if delta_y > 0 else 1
        pyautogui.scroll(scroll_direction * self._config.scroll_amount)
        self._prev_scroll_y = current_y
        return "Scrolling"

    def update(self, landmarks: Any) -> str:
        """Tek bir el karesini isleyip kullaniciya gosterilecek durumu dondurur."""
        self._toggle_pause_if_fist(landmarks)
        if self._paused:
            self.reset_gestures()
            return "Paused"

        screen_x, screen_y = self._map_to_screen(
            landmarks[INDEX_TIP].x,
            landmarks[INDEX_TIP].y,
        )
        pyautogui.moveTo(screen_x, screen_y, duration=self._config.mouse_move_duration)

        click_status = self._handle_clicks(landmarks)
        if click_status:
            return click_status

        scroll_status = self._handle_scroll(landmarks)
        return scroll_status or "Tracking Cursor"


def draw_hud(frame: Any, status: str, fps: float) -> None:
    """Kamera penceresinin ustune sade durum bilgisi cizer."""
    width = frame.shape[1]
    cv2.rectangle(frame, (0, 0), (width, 112), (18, 18, 18), -1)
    cv2.putText(
        frame,
            "Gesture Control",
        (18, 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (80, 220, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
            f"Status: {status}   FPS: {fps:.0f}",
        (18, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
            "P: Pause / Resume   Q / Esc: Quit",
        (18, 98),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.54,
        (210, 210, 210),
        1,
        cv2.LINE_AA,
    )


def create_drawing_tools(config: ControlConfig) -> tuple[Any, Any]:
    """Performans icin varsayilan olarak el iskeleti cizimini kapali tutar."""
    if not config.draw_hand_lines:
        return None, None
    return mp.solutions.drawing_utils, mp.solutions.drawing_styles


def write_pid_file() -> None:
    """stop.bat dosyasinin uygulamayi kolayca kapatabilmesi icin PID yazar."""
    PID_FILE.write_text(str(os.getpid()), encoding="ascii")


def remove_pid_file() -> None:
    """Uygulama kapanirken eski PID kaydini temizler."""
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def main() -> None:
    """Kamera, el algilama ve fare kontrolu dongusunu calistirir."""
    config = ControlConfig()
    controller = GestureMouse(config)
    camera = CameraStream(config).start()
    mp_hands = mp.solutions.hands
    mp_draw, mp_style = create_drawing_tools(config)

    if not camera.is_opened():
        camera.release()
        raise RuntimeError("Failed to open camera. Check camera permissions and other apps using it.")

    write_pid_file()
    status = "Standby"
    last_frame_at = time.monotonic()

    hands = None
    last_hand_seen_at = time.monotonic()

    def create_hands() -> Any:
        try:
            return mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=1,
                model_complexity=config.model_complexity,
                min_detection_confidence=config.detection_confidence,
                min_tracking_confidence=config.tracking_confidence,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Failed to read MediaPipe model files.\n\n"
                "This error usually occurs if the project/venv path is under OneDrive or contains special characters.\n"
                "Solution:\n"
                "- Run `install.bat` again (installs virtual environment under LOCALAPPDATA)\n"
                "- Or move the project to an ASCII path (e.g., C:\\GestureControl)\n"
            ) from exc

    try:
        hands = create_hands()
        while True:
            ok, frame = camera.read()
            if not ok:
                status = "Camera read error"
                time.sleep(0.01)
                continue

            # Ayna goruntusu kullanmak, el hareketini ekrandaki yonle uyumlu yapar.
            frame = cv2.flip(frame, 1)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # MediaPipe performans ipucu: image writeable flag.
            rgb_frame.flags.writeable = False
            result = hands.process(rgb_frame)
            rgb_frame.flags.writeable = True

            if result.multi_hand_landmarks:
                last_hand_seen_at = time.monotonic()
                hand = result.multi_hand_landmarks[0]
                try:
                    status = controller.update(hand.landmark)
                except pyautogui.FailSafeException:
                    status = "Failsafe triggered (mouse moved to corner)"
                    break

                if config.draw_hand_lines and mp_draw and mp_style:
                    mp_draw.draw_landmarks(
                        frame,
                        hand,
                        mp_hands.HAND_CONNECTIONS,
                        mp_style.get_default_hand_landmarks_style(),
                        mp_style.get_default_hand_connections_style(),
                    )
            else:
                status = "Paused" if controller.is_paused() else "Standby"
                controller.reset_gestures()

                # Bazen takip kilitlenebiliyor; uzun sure el yoksa MediaPipe'i yeniden baslat.
                if time.monotonic() - last_hand_seen_at > config.no_hand_reset_seconds:
                    try:
                        hands.close()
                    except Exception:
                        pass
                    hands = create_hands()
                    last_hand_seen_at = time.monotonic()

            now = time.monotonic()
            fps = 1 / max(now - last_frame_at, 0.001)
            last_frame_at = now

            draw_hud(frame, status, fps)
            cv2.imshow(WINDOW_TITLE, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in EXIT_KEYS:
                break
            if key == PAUSE_KEY:
                status = controller.toggle_pause()
    finally:
        if hands is not None:
            try:
                hands.close()
            except Exception:
                pass
        camera.release()
        remove_pid_file()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
