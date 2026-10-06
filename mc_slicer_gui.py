import sys
import os
import importlib.util
import threading
import math
import wave
import struct
import random as pyrandom
from pathlib import Path

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

os.environ["VTK_SILENCE_GET_VOID_POINTER_WARNINGS"] = "1"

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox,
    QGroupBox, QFrame, QCheckBox, QSlider, QScrollArea, QSpinBox,
    QTabWidget, QProgressBar, QComboBox, QDoubleSpinBox, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, QCoreApplication, QRect, QThread, pyqtSignal, QSettings, QUrl
from PyQt5.QtGui import QCursor, QFont, QPainter, QColor, QPen, QBrush

try:
    from PyQt5.QtMultimedia import QSoundEffect
except Exception:
    QSoundEffect = None

# ---------------------------------------------------------------------------
# Theming
#
# Two selectable UI themes, applied to both the boot screen and the main
# application. Persisted via QSettings so the choice is remembered across
# launches. See SlicerApp's SETTINGS tab for the picker.
# ---------------------------------------------------------------------------
THEMES = {
    "wingman": {
        "label": "Wingman HUD (Holographic Orange)",
        "bg_window": "#131310",
        "bg_panel": "#1b1c17",
        "bg_deep": "#0b0b09",
        "accent": "#ff8a1e",
        "accent_soft": "#ffc98a",
        "text_primary": "#f3f2ea",
        "text_muted": "#9b9a8f",
        "border": "#2d2e27",
        "plotter_bg": "#0d0d0a",
        "viewport_bg_bottom": "#ff8a1e",
        "viewport_bg_top": "#0c0906",
        "knife": "#eceae0",
        "spark": "#ffd27a",
        "preview_mesh": "#6b7280",
        "preview_primary": "#ff8a1e",
        "preview_secondary": "#ffc98a",
        "preview_custom": "#ffd27a",
        "preview_slot": "#ffd27a",
        "preview_current_step": "#f3f2ea",
    },
    "vice": {
        "label": "Vice City (Neon Pink/Cyan)",
        "bg_window": "#090d16",
        "bg_panel": "#0c101d",
        "bg_deep": "#03050d",
        "accent": "#ff2a6d",
        "accent_soft": "#00f0ff",
        "text_primary": "#e8f7ff",
        "text_muted": "#94a3b8",
        "border": "#1e293b",
        "plotter_bg": "#090d16",
        "viewport_bg_bottom": "#ff2a6d",
        "viewport_bg_top": "#080414",
        "knife": "#c9f8ff",
        "spark": "#ffd166",
        "preview_mesh": "#64748b",
        "preview_primary": "#ff2a6d",
        "preview_secondary": "#00f0ff",
        "preview_custom": "#ffae00",
        "preview_slot": "#ffd166",
        "preview_current_step": "#00ff9d",
    },
}

DEFAULT_THEME_KEY = "wingman"


def load_theme_key():
    """Reads the saved theme choice, falling back to the default if unset/invalid."""
    try:
        key = QSettings("MCSlicer", "FabSuite").value("ui/theme", DEFAULT_THEME_KEY)
        return key if key in THEMES else DEFAULT_THEME_KEY
    except Exception:
        return DEFAULT_THEME_KEY


def save_theme_key(key):
    try:
        QSettings("MCSlicer", "FabSuite").setValue("ui/theme", key)
    except Exception:
        pass


def hex_to_qcolor(hex_str, alpha=255):
    hex_str = hex_str.lstrip('#')
    r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
    return QColor(r, g, b, alpha)


# ---------------------------------------------------------------------------
# Themed 3D viewport backgrounds
#
# Each theme gets a procedurally generated backdrop (glowing sunset clouds for
# Wingman, a retro cyberpunk skyline/sun for Vice City) cached to disk under
# assets/backgrounds/. Requires Pillow; if it's unavailable (or generation
# fails for any reason) a plain two-color gradient is used instead so the app
# keeps working either way.
# ---------------------------------------------------------------------------
def _theme_background_path(theme_key):
    return os.path.join(script_dir, "assets", "backgrounds", f"{theme_key}.png")


def _ensure_theme_backgrounds():
    try:
        import PIL  # noqa: F401
    except Exception:
        return

    backgrounds_dir = os.path.join(script_dir, "assets", "backgrounds")
    try:
        os.makedirs(backgrounds_dir, exist_ok=True)
    except Exception:
        return

    wingman_path = _theme_background_path("wingman")
    if not os.path.exists(wingman_path):
        _generate_wingman_background(wingman_path)

    vice_path = _theme_background_path("vice")
    if not os.path.exists(vice_path):
        _generate_vice_background(vice_path)


def _generate_wingman_background(path):
    """Warm gradient sky with soft glowing cloud blobs (Wingman HUD theme)."""
    try:
        import random as _r
        from PIL import Image, ImageDraw, ImageFilter, ImageOps

        width, height = 1024, 768
        grad = Image.linear_gradient("L").resize((width, height))
        img = ImageOps.colorize(grad, black=(12, 9, 6), white=(255, 138, 30)).convert("RGBA")

        cloud_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(cloud_layer)
        rng = _r.Random(42)
        for _ in range(16):
            cx = rng.uniform(0, width)
            cy = rng.uniform(height * 0.12, height * 0.85)
            rx = rng.uniform(70, 240)
            ry = rng.uniform(24, 80)
            alpha = rng.randint(35, 85)
            draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(255, 205, 150, alpha))
        cloud_layer = cloud_layer.filter(ImageFilter.GaussianBlur(28))

        img = Image.alpha_composite(img, cloud_layer)
        img.convert("RGB").save(path)
    except Exception:
        pass


def _generate_vice_background(path):
    """Retro cyberpunk skyline with a scanline synthwave sun (Vice City theme)."""
    try:
        import random as _r
        from PIL import Image, ImageDraw, ImageFilter, ImageOps

        width, height = 1024, 768
        grad = Image.linear_gradient("L").resize((width, height))
        img = ImageOps.colorize(grad, black=(8, 4, 20), white=(255, 42, 109)).convert("RGBA")

        base_y = height * 0.86

        sun_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        sun_draw = ImageDraw.Draw(sun_layer)
        sun_cx, sun_cy, sun_r = width * 0.5, height * 0.62, height * 0.30
        sun_draw.ellipse(
            [sun_cx - sun_r, sun_cy - sun_r, sun_cx + sun_r, sun_cy + sun_r],
            fill=(255, 120, 190, 210),
        )
        band_y = sun_cy
        band_h = max(6.0, sun_r * 0.16)
        while band_y < sun_cy + sun_r:
            sun_draw.rectangle(
                [sun_cx - sun_r * 1.1, band_y, sun_cx + sun_r * 1.1, band_y + band_h * 0.4],
                fill=(0, 0, 0, 0),
            )
            band_y += band_h
        sun_layer = sun_layer.filter(ImageFilter.GaussianBlur(5))
        img = Image.alpha_composite(img, sun_layer)

        draw = ImageDraw.Draw(img, "RGBA")
        rng = _r.Random(7)
        x = 0
        while x < width:
            w = rng.randint(40, 110)
            h = rng.randint(30, 160)
            draw.rectangle([x, base_y - h, x + w, height], fill=(18, 8, 34, 235))
            x += w + rng.randint(4, 14)

        draw.line([(0, base_y), (width, base_y)], fill=(0, 240, 255, 200), width=3)

        img.convert("RGB").save(path)
    except Exception:
        pass


def apply_viewport_background(plotter, theme_key, use_image=True):
    """Applies the themed 3D-viewport backdrop: a procedurally generated image
    if one is cached (glowing clouds / cyberpunk skyline), else a plain
    two-color gradient, else a flat fallback color."""
    theme = THEMES.get(theme_key, THEMES[DEFAULT_THEME_KEY])
    bg_path = _theme_background_path(theme_key)

    if use_image and os.path.exists(bg_path):
        try:
            # Keep the background static to avoid interaction-time resize
            # callbacks that can cause soft/blurred camera motion.
            plotter.add_background_image(bg_path, scale=1.0, auto_resize=False)
            return
        except Exception:
            pass

    try:
        plotter.set_background(theme["viewport_bg_bottom"], top=theme["viewport_bg_top"])
        return
    except Exception:
        pass

    try:
        plotter.set_background(theme["plotter_bg"])
    except Exception:
        pass

# The following are slow to import (pyvista pulls in VTK, trimesh/scipy/shapely
# add more overhead, and slicer_engine imports ezdxf/cadquery). Importing them at
# module load time delayed the boot screen from appearing at all. Instead they are
# loaded lazily by HeavyLoader on a background thread *after* the boot screen is
# already on screen, so the window shows up immediately and the loading animation
# reflects real work instead of just being cosmetic.
pv = None
QtInteractor = None
np = None
trimesh = None
sg = None
R = None
random = None
slicer_engine = None
SlicerEngine = None


def _load_heavy_modules():
    """Import the slow geometry/UI libraries and wire up module-level globals.

    Safe to call from a background thread: only pure Python/NumPy imports and
    file I/O happen here, no Qt widgets are created.
    """
    global pv, QtInteractor, np, trimesh, sg, R, random, slicer_engine, SlicerEngine

    import random as _random
    random = _random

    import numpy as _np
    np = _np

    import pyvista as _pv
    pv = _pv

    from pyvistaqt import QtInteractor as _QtInteractor
    QtInteractor = _QtInteractor

    import trimesh as _trimesh
    trimesh = _trimesh

    import shapely.geometry as _sg
    sg = _sg

    from scipy.spatial.transform import Rotation as _R
    R = _R

    engine_path = os.path.join(script_dir, "slicer_engine.py")
    if not os.path.exists(engine_path):
        raise FileNotFoundError(f"Could not find slicer_engine.py at: {engine_path}")

    spec = importlib.util.spec_from_file_location("slicer_engine", engine_path)
    _engine_module = importlib.util.module_from_spec(spec)
    sys.modules["slicer_engine"] = _engine_module
    spec.loader.exec_module(_engine_module)
    slicer_engine = _engine_module
    SlicerEngine = _engine_module.SlicerEngine


_boot_sound_effect = None


def _ensure_boot_sound_file() -> str:
    """Create a short knife-slash WAV if it does not already exist."""
    sounds_dir = os.path.join(script_dir, "assets", "sounds")
    os.makedirs(sounds_dir, exist_ok=True)
    wav_path = os.path.join(sounds_dir, "knife_slash.wav")

    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1024:
        return wav_path

    sample_rate = 22050
    duration = 0.14
    total = int(sample_rate * duration)
    rng = pyrandom.Random(1337)

    with wave.open(wav_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)

        for i in range(total):
            t = i / sample_rate
            env = max(0.0, 1.0 - t / duration)
            hiss = (rng.uniform(-1.0, 1.0) * 0.75) * env
            tone = math.sin(2.0 * math.pi * (1900.0 - 900.0 * (t / duration)) * t) * 0.28 * env
            sample = max(-1.0, min(1.0, hiss + tone))
            wav.writeframesraw(struct.pack("<h", int(sample * 32767)))

    return wav_path


def play_knife_slash_sound(playback_rate: float = 1.0):
    """Play knife slash sound with Qt audio when available, then Windows fallback."""
    global _boot_sound_effect

    if QSoundEffect is not None:
        try:
            sound_path = _ensure_boot_sound_file()
            if _boot_sound_effect is None:
                _boot_sound_effect = QSoundEffect()
                _boot_sound_effect.setSource(QUrl.fromLocalFile(sound_path))
                _boot_sound_effect.setLoopCount(1)
                _boot_sound_effect.setVolume(0.35)
            _boot_sound_effect.setMuted(False)
            _boot_sound_effect.setVolume(0.35)
            _boot_sound_effect.play()
            return
        except Exception:
            pass

    # Fallback path for systems without Qt multimedia backend.
    def _sound_task():
        try:
            import winsound

            sound_path = _ensure_boot_sound_file()
            try:
                winsound.PlaySound(
                    sound_path,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                return
            except Exception:
                pass

            # System sounds are more reliable than Beep on many Windows setups.
            try:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except Exception:
                pass

            # Fall back to a short synthesized sweep if the system sound is unavailable.
            try:
                for freq in (2200, 1800, 1400, 900):
                    winsound.Beep(freq, 45)
            except Exception:
                try:
                    winsound.MessageBeep()
                except Exception:
                    pass
        except Exception:
            pass

    threading.Thread(target=_sound_task, daemon=True).start()


class HeavyLoader(QThread):
    """Loads the slow geometry libraries and the bulldog boot model off the GUI thread."""

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def run(self):
        try:
            self.progress.emit("Loading Geometry Kernel")
            _load_heavy_modules()

            # Keep boot startup fast: do not block on external mesh discovery
            # or background image generation here. The boot scene uses a
            # procedural low-poly bulldog + grid cut animation.
            mesh = None

            self.progress.emit("System Ready")
            self.finished_ok.emit(mesh)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _load_bulldog_mesh(self):
        """Locate (copying from Downloads into assets/ if needed) and load the
        bulldog boot model. Returns a trimesh.Trimesh, or None if unavailable."""
        try:
            assets_dir = os.path.join(script_dir, "assets")
            os.makedirs(assets_dir, exist_ok=True)

            bundled = None
            for ext in ('.stl', '.obj', '.ply', '.glb', '.gltf'):
                cand = os.path.join(assets_dir, 'bulldog' + ext)
                if os.path.exists(cand):
                    bundled = cand
                    break

            model_path = bundled

            if not model_path:
                downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                allowed_exts = ('.stl', '.obj', '.ply', '.glb', '.gltf')
                if os.path.isdir(downloads_dir):
                    candidates = []
                    for fname in os.listdir(downloads_dir):
                        full = os.path.join(downloads_dir, fname)
                        if not os.path.isfile(full):
                            continue
                        low = fname.lower()
                        ext = os.path.splitext(fname)[1].lower()
                        if ext not in allowed_exts or "bulldog" not in low:
                            continue
                        score = 2 if ("low" in low and "poly" in low) else (1 if "low" in low else 0)
                        candidates.append((score, fname))

                    if candidates:
                        candidates.sort(key=lambda item: -item[0])
                        fname = candidates[0][1]
                        candidate = os.path.join(downloads_dir, fname)
                        try:
                            dst = os.path.join(assets_dir, 'bulldog' + os.path.splitext(fname)[1].lower())
                            if not os.path.exists(dst):
                                import shutil
                                shutil.copyfile(candidate, dst)
                            model_path = dst
                        except Exception:
                            model_path = candidate

            if not model_path or not os.path.exists(model_path):
                return None

            mesh = trimesh.load(model_path, force='scene', process=True)
            if isinstance(mesh, trimesh.Scene):
                geometries = [g for g in mesh.geometry.values() if isinstance(g, trimesh.Trimesh)]
                if not geometries:
                    return None
                mesh = trimesh.util.concatenate(geometries)
            return mesh
        except Exception:
            return None


class BootScreen(QWidget):
    def __init__(self, on_finish):
        super().__init__()
        self.setObjectName("BootScreen")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.on_finish = on_finish
        self.theme_key = load_theme_key()
        self.theme = THEMES.get(self.theme_key, THEMES[DEFAULT_THEME_KEY])
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.resize(750, 560)
        self.center_on_screen()

        self._heavy_ready = False
        self._scene_built = False
        self._finished = False
        self._bulldog_mesh = None
        self._scene_pending = False
        self._boot_dog_actor = None
        self._grid_plane_actors = []
        self._knife_actor = None
        self._boot_center = None
        self._boot_max_span = None
        self._sparks = []
        self._boot_planes = []
        self._anim_tick = 0
        self._prev_sweep_t = 0.0
        self._scene_visible_ticks = 0
        self._knife_cut_triggered = False

        self.init_ui()
        QTimer.singleShot(120, play_knife_slash_sound)
        self.counter = 0
        self.dots = 0
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(28)

        # Kick off the real (slow) loading work now that the window is visible,
        # instead of blocking module import before any UI could show at all.
        self.loader = HeavyLoader(self)
        self.loader.progress.connect(self._on_loader_progress)
        self.loader.finished_ok.connect(self._on_loader_finished)
        self.loader.failed.connect(self._on_loader_failed)
        self.loader.start()

    def _on_loader_progress(self, message):
        self.hud_ribbon.setText(message)

    def _on_loader_finished(self, mesh):
        self._heavy_ready = True
        self._bulldog_mesh = mesh
        self._scene_pending = True

    def _on_loader_failed(self, message):
        print(f"Boot loader failed: {message}")
        self._heavy_ready = True
        if self.counter >= 100:
            self._finish_boot()

    def _build_boot_scene(self):
        if pv is None or QtInteractor is None:
            return

        try:
            self.viewport_placeholder_lbl.setParent(None)
        except Exception:
            pass

        theme = self.theme
        self.boot_plotter = QtInteractor(self.viewport_frame)
        apply_viewport_background(self.boot_plotter, self.theme_key, use_image=False)
        try:
            self.boot_plotter.enable_anti_aliasing("msaa")
        except Exception:
            pass
        self.viewport_layout.addWidget(self.boot_plotter)

        # Procedural "low poly bulldog" to avoid waiting on external files.
        center = np.array([0.0, 0.0, 0.0], dtype=float)
        max_span = 120.0

        try:
            head = pv.Sphere(radius=max_span * 0.24, center=tuple(center + np.array([0.0, 0.0, 0.0])), theta_resolution=14, phi_resolution=14)
            snout = pv.Box(bounds=(
                -max_span * 0.12,
                max_span * 0.18,
                -max_span * 0.08,
                max_span * 0.08,
                -max_span * 0.12,
                max_span * 0.02,
            ))
            ear_l = pv.Cone(center=(-max_span * 0.13, max_span * 0.02, max_span * 0.19), direction=(-0.2, -0.2, 1.0), height=max_span * 0.18, radius=max_span * 0.06)
            ear_r = pv.Cone(center=(-max_span * 0.13, -max_span * 0.02, max_span * 0.19), direction=(-0.2, 0.2, 1.0), height=max_span * 0.18, radius=max_span * 0.06)
            jaw = pv.Box(bounds=(
                -max_span * 0.03,
                max_span * 0.16,
                -max_span * 0.09,
                max_span * 0.09,
                -max_span * 0.22,
                -max_span * 0.12,
            ))

            dog_body = pv.Box(bounds=(
                -max_span * 0.16,
                max_span * 0.16,
                -max_span * 0.08,
                max_span * 0.08,
                -max_span * 0.10,
                max_span * 0.10,
            ))
            dog_head = pv.Sphere(radius=max_span * 0.16, center=tuple(center + np.array([0.0, 0.0, max_span * 0.05])), theta_resolution=16, phi_resolution=16)
            dog_mesh = dog_body.merge(dog_head)
            self._boot_dog_actor = self.boot_plotter.add_mesh(dog_mesh, color=theme["accent"], opacity=0.95, smooth_shading=True)

            halo = pv.Sphere(radius=max_span * 0.58)
            self._boot_halo = self.boot_plotter.add_mesh(halo, color=theme["accent_soft"], opacity=0.06, show_edges=False)

            # 3D cut-grid: a few translucent planes that pulse and slide.
            self._grid_plane_actors = []
            plane_specs = [
                (np.array([1.0, 0.0, 0.0]), theme["accent"], 0.30),
                (np.array([0.0, 1.0, 0.0]), theme["accent_soft"], -0.12),
                (np.array([0.0, 0.0, 1.0]), theme["text_primary"], 0.12),
            ]
            for normal, color, offset_mul in plane_specs:
                plane = pv.Plane(
                    center=tuple(center + normal * (max_span * offset_mul)),
                    direction=tuple(normal.tolist()),
                    i_size=max_span * 1.05,
                    j_size=max_span * 1.05,
                )
                actor = self.boot_plotter.add_mesh(
                    plane,
                    color=color,
                    opacity=0.18,
                    show_edges=True,
                    line_width=2,
                )
                self._grid_plane_actors.append((actor, normal.copy(), float(offset_mul)))

            knife_len = max_span * 1.55
            knife_wid = max_span * 0.30
            knife = pv.Plane(center=tuple(center), direction=(0, 1, 0), i_size=knife_len, j_size=knife_wid)
            self._knife_actor = self.boot_plotter.add_mesh(knife, color=theme["knife"], opacity=1.0, show_edges=False)

            self._sparks = []
            self._boot_center = center
            self._boot_max_span = max_span
        except Exception:
            outer_torus = pv.ParametricTorus(ringradius=2.0, crosssectionradius=0.5)
            inner_core = pv.Icosahedron(radius=1.2)
            halo = pv.Sphere(radius=1.6)
            self.boot_plotter.add_mesh(outer_torus, color=theme["accent_soft"], style="wireframe", line_width=2, opacity=0.75)
            self.boot_plotter.add_mesh(halo, color=theme["accent"], opacity=0.12, show_edges=False)
            self.boot_plotter.add_mesh(inner_core, color=theme["accent"], opacity=0.9, show_edges=True)

        self.boot_plotter.reset_camera()
        self._scene_built = True

    def _finish_boot(self):
        if self._finished:
            return
        self._finished = True
        self.timer.stop()
        try:
            if getattr(self, 'boot_plotter', None) is not None:
                self.boot_plotter.close()
        except Exception:
            pass
        self.close()
        self.on_finish()

    def center_on_screen(self):
        qr = self.frameGeometry()
        screen = QApplication.primaryScreen()
        if screen:
            cp = screen.availableGeometry().center()
            qr.moveCenter(cp)
            self.move(qr.topLeft())

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setAlignment(Qt.AlignCenter)
        theme = self.theme

        self.setStyleSheet(f"""
            QWidget#BootScreen {{
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {theme['bg_deep']}, stop:1 {theme['bg_window']});
                border: 2px solid {theme['accent']};
                border-radius: 16px;
                color: {theme['accent']};
            }}
        """)

        self.title_lbl = QLabel("MC SLICER")
        self.title_lbl.setStyleSheet(f"font-size: 30px; font-weight: bold; color: {theme['accent']}; letter-spacing: 6px; border: none; text-transform: uppercase;")
        self.title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_lbl)

        subtitle = QLabel("ADVANCED FABRICATION PLATFORM")
        subtitle.setStyleSheet(f"font-size: 11px; color: {theme['text_muted']}; letter-spacing: 3px; border: none; font-weight: bold;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(10)

        # Lightweight placeholder shown instantly. The real pyvista viewport
        # (bulldog model + knife-slice animation) is built once the background
        # HeavyLoader finishes importing pyvista/trimesh, so the window never
        # has to wait on those slow imports before appearing.
        self.viewport_frame = QFrame(self)
        self.viewport_frame.setFixedHeight(240)
        self.viewport_frame.setStyleSheet(f"background-color: {theme['plotter_bg']}; border: 1px solid {theme['border']}; border-radius: 8px;")
        self.viewport_layout = QVBoxLayout(self.viewport_frame)
        self.viewport_layout.setContentsMargins(0, 0, 0, 0)

        self.viewport_placeholder_lbl = QLabel("Initializing Fabrication Engine")
        self.viewport_placeholder_lbl.setAlignment(Qt.AlignCenter)
        self.viewport_placeholder_lbl.setStyleSheet(f"color: {theme['accent']}; font-size: 13px; font-weight: bold; border: none; letter-spacing: 2px;")
        self.viewport_layout.addWidget(self.viewport_placeholder_lbl)

        layout.addWidget(self.viewport_frame)
        layout.addSpacing(10)

        self.status_lbl = QLabel("Initializing Fabrication Engine...")
        self.status_lbl.setStyleSheet(f"font-size: 13px; color: {theme['text_muted']}; font-weight: bold; border: none;")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_lbl)

        self.hud_ribbon = QLabel("System Standby")
        self.hud_ribbon.setStyleSheet(f"font-size: 10px; color: {theme['accent']}; border: 1px solid {theme['accent']}; padding: 6px 10px; letter-spacing: 3px;")
        self.hud_ribbon.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hud_ribbon)

        layout.addSpacing(6)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {theme['bg_panel']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {theme['accent']}, stop:1 {theme['accent_soft']});
                border-radius: 4px;
            }}
        """)
        layout.addWidget(self.progress_bar)

        self.scanline_offset = 0
        self.pulse_phase = 0.0
        self.sweep_position = 0

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        theme = self.theme

        self.scanline_offset = (self.scanline_offset + 3) % 12
        for y in range(self.scanline_offset, rect.height(), 12):
            painter.setPen(QPen(hex_to_qcolor(theme['accent'], 18), 1))
            painter.drawLine(0, y, rect.width(), y)

        pulse = 0.55 + 0.45 * abs((self.pulse_phase % 2.0) - 1.0)
        self.pulse_phase += 0.18
        glow = int(80 + pulse * 60)
        painter.setPen(QPen(hex_to_qcolor(theme['accent_soft'], glow), 1))
        painter.drawRect(8, 8, rect.width() - 16, rect.height() - 16)

        self.sweep_position = (self.sweep_position + 8) % (rect.width() + 120) - 60
        painter.setPen(QPen(hex_to_qcolor(theme['accent'], 70), 2))
        painter.drawLine(self.sweep_position, 16, self.sweep_position + 60, 16)

    def update_animation(self):
        if self.counter < 100:
            self.counter += 2
        self._anim_tick += 2
        self.progress_bar.setValue(min(self.counter, 100))

        boot_plotter = getattr(self, 'boot_plotter', None)

        if boot_plotter is not None:
            try:
                boot_plotter.camera.Azimuth(4.0)
                boot_plotter.camera.Elevation(1.5)
            except Exception:
                pass

        if self._scene_pending and self._heavy_ready and self.counter >= 80:
            self._scene_pending = False
            self._build_boot_scene()

        if self._scene_built and self._boot_dog_actor is not None:
            try:
                self._boot_dog_actor.RotateZ(3.5)
            except Exception:
                pass

        if self._scene_built and self._grid_plane_actors and self._boot_center is not None:
            phase = (self._anim_tick % 200) / 200.0
            wave = math.sin(phase * 2.0 * math.pi)
            for actor, normal, base_mul in self._grid_plane_actors:
                try:
                    d = (base_mul + 0.05 * wave) * self._boot_max_span
                    actor.SetPosition(*(self._boot_center + normal * d))
                except Exception:
                    pass

        # animate boot-screen knife and sparks once the real scene is built
        if self._scene_built and self._knife_actor is not None and self._boot_center is not None and boot_plotter is not None:
            t = (self._anim_tick % 100) / 100.0
            x_off = (t * 2.0 - 1.0) * (self._boot_max_span * 0.82)

            crossed_center = self._prev_sweep_t < 0.5 <= t
            self._prev_sweep_t = t

            try:
                self._knife_actor.SetPosition(*(self._boot_center + np.array([x_off, 0.0, 0.0])))

                if crossed_center:
                    self._knife_cut_triggered = True
                    play_knife_slash_sound(playback_rate=1.06)
                    for i in range(6):
                        off = np.array([random.uniform(-0.06, 0.06), random.uniform(-0.02, 0.02), random.uniform(-0.06, 0.06)]) * self._boot_max_span
                        sph = pv.Sphere(radius=self._boot_max_span * 0.02, center=tuple(self._boot_center + np.array([x_off, 0.0, 0.0]) + off))
                        actor = boot_plotter.add_mesh(sph, color=self.theme["spark"], opacity=1.0, show_edges=False)
                        self._sparks.append([actor, 10])
            except Exception:
                pass

            # age sparks
            new_sparks = []
            for actor, life in list(self._sparks):
                life -= 1
                if life <= 0:
                    try:
                        boot_plotter.remove_actor(actor)
                    except Exception:
                        pass
                else:
                    new_sparks.append([actor, life])
            self._sparks = new_sparks

        if boot_plotter is not None:
            try:
                boot_plotter.render()
            except Exception:
                pass

        ready = self._heavy_ready and self._scene_built

        if self._scene_built:
            self._scene_visible_ticks += 1

        if self.counter < 30:
            status = "Loading Geometry Kernel"
            self.hud_ribbon.setText("Kernel Linked")
        elif self.counter < 60:
            status = "Configuring Viewport Pipeline"
            self.hud_ribbon.setText("Viewport Synced")
        elif self.counter < 90:
            status = "Calibrating Assembly Data"
            self.hud_ribbon.setText("Assembly Calibrated")
        elif not ready:
            status = "Finalizing Startup"
            self.hud_ribbon.setText("Awaiting Kernel Sync")
        else:
            status = "System Ready"
            self.hud_ribbon.setText("Ready For Fabrication")

        self.dots = (self.dots + 1) % 4
        cursor = ["|", "/", "-", "\\"][self.counter % 4]
        self.status_lbl.setText(status + "." * self.dots + cursor)

        pulse_color = self.theme["accent"] if self.counter % 4 < 2 else self.theme["accent_soft"]
        self.title_lbl.setStyleSheet(
            f"font-size: 30px; font-weight: bold; color: {pulse_color}; letter-spacing: 6px; border: none; text-transform: uppercase;"
        )

        # Keep boot scene visible long enough to actually show the spinning
        # bulldog + one knife-cut event with sound before handing off to the app.
        min_scene_ticks = 70  # ~2.0 seconds at 28ms timer
        scene_showcase_done = (self._scene_visible_ticks >= min_scene_ticks) and (
            self._knife_cut_triggered or self._knife_actor is None
        )

        if self.counter >= 100 and ready and scene_showcase_done:
            self._finish_boot()


class DropArea(QFrame):
    def __init__(self, parent=None, callback=None, theme=None):
        
        super().__init__(parent)
        self.callback = callback
        self.theme = theme or THEMES[DEFAULT_THEME_KEY]
        self.setAcceptDrops(True)
        self.setMinimumHeight(115)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        self.label = QLabel("📁 Click or Drop 3D Model File\n(.stl, .obj, .3mf, .step, .ply)", self)
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)
        self.apply_theme(self.theme)

    def apply_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"""
            QFrame {{
                border: 2px dashed {theme['accent']};
                border-radius: 8px;
                background-color: {theme['bg_panel']};
            }}
            QFrame:hover {{
                border-color: {theme['accent_soft']};
                background-color: {theme['bg_deep']};
            }}
        """)
        self.label.setStyleSheet(f"color: {theme['accent']}; font-size: 15px; font-weight: bold; border: none;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            file_filter = "Supported 3D Files (*.stl *.obj *.3mf *.step *.stp *.ply *.off *.dae *.dxf);;All Files (*.*)"
            file_path, _ = QFileDialog.getOpenFileName(self, "Select 3D Model File", "", file_filter)
            if file_path and self.callback:
                self.callback(file_path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if self.callback:
                self.callback(file_path)


class SlicerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MC Slicer - Fabrication Suite")
        self.resize(1600, 900)
        self.setMinimumSize(1200, 700)

        self.theme_key = load_theme_key()

        self.engine = None
        self.current_file = None
        self.planes = []
        self.assembly_steps = []
        self.assembly_plan = []
        self.assembly_plan_mode = "interlocking"
        self.assembly_step_index = 0
        self.current_step = 1
        self.preview_mode = "all"

        self.init_ui()
        self.apply_theme()

    @property
    def theme(self):
        return THEMES.get(self.theme_key, THEMES[DEFAULT_THEME_KEY])

    def get_engine_bounds(self):
        if hasattr(self.engine, 'get_bounds_size'):
            return self.engine.get_bounds_size()
        elif hasattr(self.engine, 'mesh'):
            mesh = self.engine.mesh
            if hasattr(mesh, 'extents'):
                exts = mesh.extents
                return (exts[1] - exts[0], exts[3] - exts[2], exts[5] - exts[4])
            elif hasattr(mesh, 'bounds'):
                b = mesh.bounds
                return (b[1] - b[0], b[3] - b[2], b[5] - b[4])
        return (100.0, 100.0, 100.0)

    def create_scrollable_tab(self, layout_content):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        wrapper_layout = QVBoxLayout(container)
        wrapper_layout.setContentsMargins(20, 16, 20, 16)
        
        inner_widget = QWidget()
        inner_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout_content.setAlignment(Qt.AlignTop)
        inner_widget.setLayout(layout_content)
        
        wrapper_layout.addWidget(inner_widget)
        wrapper_layout.addStretch(1)
        
        scroll.setWidget(container)
        return scroll

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        split_layout = QHBoxLayout(main_widget)
        split_layout.setContentsMargins(16, 16, 16, 16)
        split_layout.setSpacing(16)

        # LEFT PANEL
        left_panel = QWidget()
        left_panel.setMinimumWidth(500)
        left_panel.setMaximumWidth(620)
        left_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        main_layout = QVBoxLayout(left_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)

        # Top Header Bar
        header_layout = QHBoxLayout()
        self.title_label = QLabel("MC SLICER <span style='font-size: 13px;'>· FABRICATION SUITE</span>")
        self.title_label.setObjectName("TitleLabel")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        reboot_btn = QPushButton("Reboot")
        reboot_btn.setFixedWidth(100)
        reboot_btn.clicked.connect(self.close)
        header_layout.addWidget(reboot_btn)
        main_layout.addLayout(header_layout)

        self.tabs = QTabWidget()

        # 1. IMPORT TAB
        import_layout = QVBoxLayout()
        import_layout.setSpacing(20)

        self.drop_area = DropArea(callback=self.load_file, theme=self.theme)
        import_layout.addWidget(self.drop_area)

        self.status_label = QLabel("Status: No file loaded")
        self.status_label.setStyleSheet(f"color: {self.theme['text_muted']}; font-weight: bold; font-size: 13px;")
        import_layout.addWidget(self.status_label)

        size_group = QGroupBox("1. Real-World Model Dimensions (mm)")
        size_layout = QVBoxLayout(size_group)
        dim_layout = QHBoxLayout()

        vbox_x = QVBoxLayout()
        vbox_x.addWidget(QLabel("X Width:"))
        self.dim_x = QLineEdit("100.0")
        self.dim_x.editingFinished.connect(lambda: self.on_size_changed('x'))
        vbox_x.addWidget(self.dim_x)
        dim_layout.addLayout(vbox_x)

        vbox_y = QVBoxLayout()
        vbox_y.addWidget(QLabel("Y Depth:"))
        self.dim_y = QLineEdit("100.0")
        self.dim_y.editingFinished.connect(lambda: self.on_size_changed('y'))
        vbox_y.addWidget(self.dim_y)
        dim_layout.addLayout(vbox_y)

        vbox_z = QVBoxLayout()
        vbox_z.addWidget(QLabel("Z Height:"))
        self.dim_z = QLineEdit("100.0")
        self.dim_z.editingFinished.connect(lambda: self.on_size_changed('z'))
        vbox_z.addWidget(self.dim_z)
        dim_layout.addLayout(vbox_z)

        size_layout.addLayout(dim_layout)
        self.lock_aspect = QCheckBox("Lock Uniform Scale Ratio")
        self.lock_aspect.setChecked(True)
        size_layout.addWidget(self.lock_aspect)
        import_layout.addWidget(size_group)

        self.tabs.addTab(self.create_scrollable_tab(import_layout), "IMPORT")

        # 2. SLICE TAB (rebuilt from scratch)
        slice_layout = QVBoxLayout()
        slice_layout.setSpacing(16)

        self.intro_label = QLabel(
            "Fusion-style workflow: configure distribution, save plane batch, preview cut parts, then review assembly steps."
        )
        self.intro_label.setWordWrap(True)
        self.intro_label.setStyleSheet(f"color: {self.theme['text_muted']}; font-size: 13px; border: none;")
        slice_layout.addWidget(self.intro_label)

        self.step_indicator = QLabel("Setup Ready · Save a plane batch to begin")
        self.step_indicator.setStyleSheet(f"color: {self.theme['accent']}; font-weight: bold; border: none;")
        slice_layout.addWidget(self.step_indicator)

        controls_group = QGroupBox("Slice Setup")
        controls_layout = QVBoxLayout(controls_group)

        dist_row = QHBoxLayout()
        distribution_label = QLabel("Distribution Method:")
        distribution_label.setWordWrap(True)
        distribution_label.setMinimumWidth(120)
        dist_row.addWidget(distribution_label)
        self.slice_method_combo = QComboBox()
        self.slice_method_combo.addItem("By Count (Fusion-style)", "count")
        self.slice_method_combo.addItem("By Spacing", "spacing")
        self.slice_method_combo.addItem("By Sheet Thickness", "thickness")
        self.slice_method_combo.setCurrentIndex(0)
        self.slice_method_combo.currentIndexChanged.connect(self.update_live_preview)
        dist_row.addWidget(self.slice_method_combo)
        controls_layout.addLayout(dist_row)

        axis_row = QHBoxLayout()
        axis_label = QLabel("Slice Axis:")
        axis_label.setMinimumWidth(75)
        axis_row.addWidget(axis_label)
        self.combo_axis = QComboBox()
        self.combo_axis.addItems(["1st Axis (X)", "2nd Axis (Y)", "3rd Axis (Z)"])
        self.combo_axis.currentIndexChanged.connect(self.update_live_preview)
        axis_row.addWidget(self.combo_axis)

        family_label = QLabel("Family:")
        family_label.setMinimumWidth(55)
        axis_row.addWidget(family_label)
        self.combo_family = QComboBox()
        self.combo_family.addItem("Primary", "primary")
        self.combo_family.addItem("Secondary", "secondary")
        self.combo_family.addItem("Custom", "custom")
        self.combo_family.currentIndexChanged.connect(self.update_live_preview)
        axis_row.addWidget(self.combo_family)
        controls_layout.addLayout(axis_row)

        dist_values_row = QHBoxLayout()
        count_box = QVBoxLayout()
        count_box.addWidget(QLabel("Slice Count:"))
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 100)
        self.spin_count.setValue(10)
        self.spin_count.valueChanged.connect(self.update_live_preview)
        count_box.addWidget(self.spin_count)
        dist_values_row.addLayout(count_box)

        spacing_box = QVBoxLayout()
        spacing_box.addWidget(QLabel("Spacing (mm):"))
        self.spin_spacing = QDoubleSpinBox()
        self.spin_spacing.setRange(0.1, 1000.0)
        self.spin_spacing.setValue(10.0)
        self.spin_spacing.setDecimals(3)
        self.spin_spacing.valueChanged.connect(self.update_live_preview)
        spacing_box.addWidget(self.spin_spacing)
        dist_values_row.addLayout(spacing_box)

        controls_layout.addLayout(dist_values_row)

        tilt_row = QHBoxLayout()
        tilt_row.addWidget(QLabel("Slice Tilt (deg):"))
        self.slider_tilt = QSlider(Qt.Horizontal)
        self.slider_tilt.setRange(-90, 90)
        self.slider_tilt.setValue(0)
        self.slider_tilt.valueChanged.connect(self.update_live_preview)
        tilt_row.addWidget(self.slider_tilt)
        controls_layout.addLayout(tilt_row)

        controls_btns = QHBoxLayout()
        self.btn_commit_planes = QPushButton("Save Plane Batch")
        self.btn_commit_planes.setObjectName("AccentButton")
        self.btn_commit_planes.clicked.connect(self.commit_current_planes)
        controls_btns.addWidget(self.btn_commit_planes)

        self.btn_clear_planes = QPushButton("Clear Saved Planes")
        self.btn_clear_planes.clicked.connect(self.clear_all_planes)
        controls_btns.addWidget(self.btn_clear_planes)
        controls_layout.addLayout(controls_btns)

        self.saved_planes_label = QLabel("Saved Planes: none")
        self.saved_planes_label.setWordWrap(True)
        self.saved_planes_label.setStyleSheet(f"color: {self.theme['text_muted']}; border: none;")
        controls_layout.addWidget(self.saved_planes_label)
        slice_layout.addWidget(controls_group)

        process_group = QGroupBox("Slice & Notch")
        process_layout = QVBoxLayout(process_group)

        material_row = QHBoxLayout()
        thick_box = QVBoxLayout()
        thick_box.addWidget(QLabel("Material Thickness (mm):"))
        self.mat_thick_input = QLineEdit("3.00")
        thick_box.addWidget(self.mat_thick_input)
        material_row.addLayout(thick_box)

        kerf_box = QVBoxLayout()
        kerf_box.addWidget(QLabel("Kerf (mm):"))
        self.kerf_input = QLineEdit("0.15")
        kerf_box.addWidget(self.kerf_input)
        material_row.addLayout(kerf_box)
        process_layout.addLayout(material_row)

        self.enable_notches = QCheckBox("Enable Interlocking Notches")
        self.enable_notches.setChecked(True)
        process_layout.addWidget(self.enable_notches)

        notch_type_row = QHBoxLayout()
        notch_type_row.addWidget(QLabel("Notch Type:"))
        self.notch_shape = QComboBox()
        self.notch_shape.addItem("Rectangular", "rectangular")
        self.notch_shape.addItem("Rounded", "rounded")
        self.notch_shape.addItem("Dovetail", "dovetail")
        self.notch_shape.addItem("T-Slot", "tslot")
        notch_type_row.addWidget(self.notch_shape)
        process_layout.addLayout(notch_type_row)

        notch_row = QHBoxLayout()
        notch_count_box = QVBoxLayout()
        notch_count_box.addWidget(QLabel("Notches / Crossing:"))
        self.notch_count = QSpinBox()
        self.notch_count.setRange(1, 10)
        self.notch_count.setValue(1)
        notch_count_box.addWidget(self.notch_count)
        notch_row.addLayout(notch_count_box)

        notch_width_box = QVBoxLayout()
        notch_width_box.addWidget(QLabel("Notch Width (0 auto):"))
        self.notch_width = QDoubleSpinBox()
        self.notch_width.setRange(0.0, 1000.0)
        self.notch_width.setDecimals(3)
        self.notch_width.setSingleStep(0.1)
        self.notch_width.setValue(0.0)
        notch_width_box.addWidget(self.notch_width)
        notch_row.addLayout(notch_width_box)
        process_layout.addLayout(notch_row)

        notch_row_2 = QHBoxLayout()
        notch_depth_box = QVBoxLayout()
        notch_depth_box.addWidget(QLabel("Notch Depth (0 auto):"))
        self.notch_depth = QDoubleSpinBox()
        self.notch_depth.setRange(0.0, 1000.0)
        self.notch_depth.setDecimals(3)
        self.notch_depth.setSingleStep(0.5)
        self.notch_depth.setValue(0.0)
        notch_depth_box.addWidget(self.notch_depth)
        notch_row_2.addLayout(notch_depth_box)

        clearance_box = QVBoxLayout()
        clearance_box.addWidget(QLabel("Fit Clearance (mm):"))
        self.fit_clearance = QDoubleSpinBox()
        self.fit_clearance.setRange(-2.0, 10.0)
        self.fit_clearance.setDecimals(3)
        self.fit_clearance.setSingleStep(0.05)
        self.fit_clearance.setValue(0.10)
        clearance_box.addWidget(self.fit_clearance)
        notch_row_2.addLayout(clearance_box)
        process_layout.addLayout(notch_row_2)

        preview_actions = QVBoxLayout()
        preview_actions_row_1 = QHBoxLayout()
        self.btn_preview_slices = QPushButton("Preview Slice Parts")
        self.btn_preview_slices.setMinimumHeight(42)
        self.btn_preview_slices.clicked.connect(self.preview_final_slices)
        preview_actions_row_1.addWidget(self.btn_preview_slices)

        self.btn_generate_assembly_steps = QPushButton("Build Assembly Steps")
        self.btn_generate_assembly_steps.setMinimumHeight(42)
        self.btn_generate_assembly_steps.clicked.connect(self.generate_assembly_steps_preview)
        preview_actions_row_1.addWidget(self.btn_generate_assembly_steps)
        preview_actions.addLayout(preview_actions_row_1)

        assembly_engine_row = QHBoxLayout()
        assembly_engine_row.addWidget(QLabel("Assembly Engine:"))
        self.assembly_engine_combo = QComboBox()
        self.assembly_engine_combo.addItem("Interlocking Slices", "interlocking")
        self.assembly_engine_combo.addItem("Welded Polygon (Beta)", "welded")
        assembly_engine_row.addWidget(self.assembly_engine_combo)
        preview_actions.addLayout(assembly_engine_row)
        process_layout.addLayout(preview_actions)

        view_mode_row = QVBoxLayout()
        view_mode_row_top = QHBoxLayout()
        self.btn_show_all = QPushButton("Show All Parts")
        self.btn_show_all.setCheckable(True)
        self.btn_show_all.setChecked(True)
        self.btn_show_all.clicked.connect(lambda: self.set_preview_mode("all"))
        view_mode_row_top.addWidget(self.btn_show_all)

        self.btn_show_assembly = QPushButton("Show Assembly")
        self.btn_show_assembly.setCheckable(True)
        self.btn_show_assembly.clicked.connect(lambda: self.set_preview_mode("assembly"))
        view_mode_row_top.addWidget(self.btn_show_assembly)
        view_mode_row.addLayout(view_mode_row_top)

        view_mode_row_bottom = QHBoxLayout()
        reset_btn = QPushButton("Reset Assembly Preview")
        reset_btn.clicked.connect(self.reset_preview)
        view_mode_row_bottom.addWidget(reset_btn)
        view_mode_row.addLayout(view_mode_row_bottom)
        process_layout.addLayout(view_mode_row)

        self.assembly_steps_label = QLabel("Assembly step: 0 / 0")
        self.assembly_steps_label.setStyleSheet(f"color: {self.theme['text_muted']}; font-weight: bold;")
        process_layout.addWidget(self.assembly_steps_label)

        self.assembly_validation_label = QLabel("Physical fit: not checked")
        self.assembly_validation_label.setWordWrap(True)
        self.assembly_validation_label.setStyleSheet(f"color: {self.theme['text_muted']}; font-weight: bold;")
        process_layout.addWidget(self.assembly_validation_label)

        self.assembly_steps_slider = QSlider(Qt.Horizontal)
        self.assembly_steps_slider.setRange(0, 0)
        self.assembly_steps_slider.setEnabled(False)
        self.assembly_steps_slider.valueChanged.connect(self.update_assembly_step_preview)
        process_layout.addWidget(self.assembly_steps_slider)

        slice_layout.addWidget(process_group)

        self.tabs.addTab(self.create_scrollable_tab(slice_layout), "SLICE")

        # 3. SHEETS & OUTPUT TAB
        output_layout = QVBoxLayout()
        output_layout.setSpacing(20)

        sheet_group = QGroupBox("Fabrication Sheet Geometry")
        sheet_form = QVBoxLayout(sheet_group)
        sheet_form.addWidget(QLabel("Sheet Dimensions W x H (mm):"))

        sheet_dim_layout = QHBoxLayout()
        self.sheet_w = QSpinBox()
        self.sheet_w.setRange(50, 4000)
        self.sheet_w.setValue(300)
        self.sheet_h = QSpinBox()
        self.sheet_h.setRange(50, 4000)
        self.sheet_h.setValue(200)
        sheet_dim_layout.addWidget(self.sheet_w)
        sheet_dim_layout.addWidget(self.sheet_h)
        sheet_form.addLayout(sheet_dim_layout)
        output_layout.addWidget(sheet_group)

        export_mode_group = QGroupBox("Export Mode")
        export_mode_layout = QVBoxLayout(export_mode_group)

        self.export_nested_dxf = QCheckBox(
            "Export nested 2D DXF fabrication layout"
        )
        self.export_nested_dxf.setChecked(True)
        export_mode_layout.addWidget(self.export_nested_dxf)

        self.export_individual_dxf = QCheckBox(
            "Export one named 2D DXF per part"
        )
        self.export_individual_dxf.setChecked(True)
        export_mode_layout.addWidget(self.export_individual_dxf)

        self.export_3d_step = QCheckBox(
            "Export all extruded parts as a 3D STEP assembly"
        )
        self.export_3d_step.setChecked(True)
        export_mode_layout.addWidget(self.export_3d_step)

        self.export_nested_3d_step = QCheckBox(
            "Export nested flat shapes as extruded 3D STEP parts"
        )
        self.export_nested_3d_step.setChecked(False)
        export_mode_layout.addWidget(self.export_nested_3d_step)

        self.export_flat_pattern_dxf = QCheckBox(
            "Export polygonized flat pattern DXF (CUT + BEND layers)"
        )
        self.export_flat_pattern_dxf.setChecked(False)
        export_mode_layout.addWidget(self.export_flat_pattern_dxf)

        self.export_solid_bend_package = QCheckBox(
            "Export solid bend package (solid + bend sketch DXF + try Inventor IPT)"
        )
        self.export_solid_bend_package.setChecked(False)
        export_mode_layout.addWidget(self.export_solid_bend_package)

        output_layout.addWidget(export_mode_group)

        # Naming & Export Options
        naming_group = QGroupBox("Part Filename Format")
        naming_layout = QVBoxLayout(naming_group)

        self.filename_template_combo = QComboBox()
        self.filename_template_combo.addItem("Axis_Slice (e.g. X_001)", "{axis}_{slice:03d}")
        self.filename_template_combo.addItem("Family_Order (e.g. PRI_005)", "{family}_{order:03d}")
        self.filename_template_combo.addItem("Label (use piece label)", "{label}")
        self.filename_template_combo.addItem("Custom...", "custom")
        self.filename_template_combo.currentIndexChanged.connect(lambda: self._on_template_change())
        naming_layout.addWidget(self.filename_template_combo)

        self.custom_filename_template = QLineEdit("{axis}_{slice:03d}")
        self.custom_filename_template.setEnabled(False)
        naming_layout.addWidget(self.custom_filename_template)

        naming_layout.addWidget(QLabel("Template tokens: {axis} {slice} {order} {label} {family} {id} {project}"))

        output_layout.addWidget(naming_group)
        self._on_template_change()

        # Preview button
        preview_btn = QPushButton("Preview Filenames")
        preview_btn.clicked.connect(self.show_filename_preview)
        preview_btn.setToolTip("Build and edit the filenames that will be applied to exported parts.")
        output_layout.addWidget(preview_btn)

        self.btn_process = QPushButton(
            "Compile and Export Parts"
        )
        self.btn_process.setMinimumHeight(50)
        self.btn_process.clicked.connect(
            self.process_and_export
        )
        output_layout.addWidget(self.btn_process)


        self.tabs.addTab(self.create_scrollable_tab(output_layout), "EXPORT")

        # 4. SETTINGS TAB
        settings_layout = QVBoxLayout()
        settings_layout.setSpacing(20)

        appearance_group = QGroupBox("Appearance")
        appearance_layout = QVBoxLayout(appearance_group)
        appearance_layout.addWidget(QLabel("UI Theme:"))

        self.theme_combo = QComboBox()
        for key, data in THEMES.items():
            self.theme_combo.addItem(data["label"], key)
        combo_index = self.theme_combo.findData(self.theme_key)
        if combo_index >= 0:
            self.theme_combo.setCurrentIndex(combo_index)
        self.theme_combo.currentIndexChanged.connect(self.on_theme_changed)
        appearance_layout.addWidget(self.theme_combo)

        theme_note = QLabel("Theme changes apply immediately and are remembered next launch.")
        theme_note.setWordWrap(True)
        self.theme_note = theme_note
        appearance_layout.addWidget(theme_note)

        settings_layout.addWidget(appearance_group)

        self.tabs.addTab(self.create_scrollable_tab(settings_layout), "SETTINGS")

        main_layout.addWidget(self.tabs)
        
        # RIGHT PANEL (Live 3D Viewport)
        self.plotter = QtInteractor(main_widget)
        apply_viewport_background(self.plotter, self.theme_key, use_image=False)
        try:
            self.plotter.enable_anti_aliasing("msaa")
        except Exception:
            pass
        self.plotter.add_axes()
        
        split_layout.addWidget(left_panel, 1) 
        split_layout.addWidget(self.plotter, 2) 
        self.refresh_slice_tab_status()

    def on_theme_changed(self, index):
        key = self.theme_combo.itemData(index)
        if key not in THEMES:
            return
        self.theme_key = key
        save_theme_key(key)
        self.apply_theme()
        self.drop_area.apply_theme(self.theme)

        theme = self.theme
        self.intro_label.setStyleSheet(f"color: {theme['text_muted']}; font-size: 13px; border: none;")
        self.step_indicator.setStyleSheet(f"color: {theme['accent']}; font-weight: bold; border: none;")
        self.assembly_steps_label.setStyleSheet(f"color: {theme['text_muted']}; font-weight: bold;")
        if self.current_file:
            self.status_label.setStyleSheet(f"color: {theme['accent']}; font-weight: bold; font-size: 13px;")
        else:
            self.status_label.setStyleSheet(f"color: {theme['text_muted']}; font-weight: bold; font-size: 13px;")

        apply_viewport_background(self.plotter, self.theme_key, use_image=False)
        if self.preview_mode == "assembly" and self.assembly_steps:
            self.update_assembly_step_preview(self.assembly_step_index)
        else:
            self.update_live_preview()

    def set_preview_mode(self, mode):
        self.preview_mode = mode
        self.btn_show_all.setChecked(mode == "all")
        self.btn_show_assembly.setChecked(mode == "assembly")
        if self.preview_mode == "assembly" and self.assembly_steps:
            self.update_assembly_step_preview(self.assembly_step_index)
        else:
            self.update_live_preview()

    def reset_preview(self):
        self.assembly_steps = []
        self.assembly_plan = []
        self.assembly_plan_mode = "interlocking"
        self._assembly_invalid_labels = set()
        self.assembly_step_index = 0
        self.assembly_steps_slider.setRange(0, 0)
        self.assembly_steps_slider.setEnabled(False)
        self.assembly_steps_label.setText("Assembly step: 0 / 0")
        self.assembly_validation_label.setText("Physical fit: not checked")
        self.assembly_validation_label.setStyleSheet(f"color: {self.theme['text_muted']}; font-weight: bold;")
        self.refresh_slice_tab_status()
        self.update_live_preview()

    def apply_theme(self):
        theme = self.theme
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {theme['bg_window']};
                color: {theme['text_primary']};
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 15px;
            }}
            QLabel#TitleLabel {{
                color: {theme['accent']};
                font-size: 18px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QTabWidget::pane {{
                border: 1px solid {theme['border']};
                background-color: {theme['bg_panel']};
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background-color: {theme['bg_panel']};
                color: {theme['text_muted']};
                padding: 12px 22px;
                border-bottom: 2px solid transparent;
                font-weight: bold;
                font-size: 15px;
            }}
            QTabBar::tab:selected {{
                color: {theme['accent']};
                border-bottom: 2px solid {theme['accent']};
                background-color: {theme['bg_window']};
            }}
            QTabBar::tab:hover {{
                color: {theme['accent']};
            }}
            QGroupBox {{
                border: 1px solid {theme['border']};
                border-radius: 8px;
                margin-top: 16px;
                background-color: {theme['bg_panel']};
                font-weight: bold;
                color: {theme['accent']};
                padding-top: 18px;
                font-size: 15px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
            }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
                background-color: {theme['bg_deep']};
                border: 1px solid {theme['border']};
                color: {theme['accent']};
                padding: 7px 9px;
                min-height: 24px;
                border-radius: 6px;
                font-size: 15px;
            }}
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
                border: 1px solid {theme['accent']};
            }}
            QPushButton {{
                background-color: {theme['bg_panel']};
                color: {theme['accent']};
                border: 1px solid {theme['accent']};
                font-weight: bold;
                border-radius: 6px;
                padding: 10px 18px;
                font-size: 15px;
            }}
            QPushButton:hover {{
                background-color: {theme['accent']};
                color: {theme['bg_window']};
            }}
            QPushButton#AccentButton {{
                background-color: {theme['accent']};
                color: {theme['bg_window']};
            }}
            QPushButton#AccentButton:hover {{
                background-color: {theme['accent_soft']};
                color: {theme['bg_window']};
            }}
            QCheckBox {{
                color: {theme['text_muted']};
                font-weight: bold;
                font-size: 15px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                background: {theme['bg_deep']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
            }}
            QCheckBox::indicator:checked {{
                background: {theme['accent']};
                border: 1px solid {theme['accent']};
            }}
            QSlider::groove:horizontal {{
                height: 8px;
                background: {theme['border']};
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {theme['accent']};
                width: 20px;
                margin-top: -6px;
                margin-bottom: -6px;
                border-radius: 10px;
            }}
        """)

    def _on_template_change(self):
        key = self.filename_template_combo.currentData()
        if key == "custom":
            self.custom_filename_template.setEnabled(True)
        else:
            self.custom_filename_template.setEnabled(False)
            self.custom_filename_template.setText(key)

    def _format_part_filename(self, piece, template_raw, project_name, seq_index=None):
        import re

        axis = str(piece.get("display_axis", piece.get("axis", "X")))
        order = int(piece.get("order", seq_index or 0))
        family = str(piece.get("family", "part")).upper()
        label = str(piece.get("label", f"part_{order:03d}"))
        pid = str(piece.get("id", label))

        # derive slice number from label if possible, else use order
        m = re.search(r"(\d+)(?!.*\d)", label)
        slice_num = int(m.group(1)) if m else order

        try:
            filename = template_raw.format(
                axis=axis,
                slice=slice_num,
                order=order,
                label=label,
                family=family,
                id=pid,
                project=project_name,
            )
        except Exception:
            filename = f"{project_name}_{order:03d}"

        # sanitize
        if hasattr(self.engine, "_safe_filename"):
            safe = self.engine._safe_filename(filename)
        else:
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)

        return safe

    def show_filename_preview(self):
        if not self.engine or not self.planes:
            QMessageBox.warning(self, "No Parts", "Load a model and generate slices first.")
            return

        try:
            from pathlib import Path

            pieces = self.build_parts()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not build parts:\n{e}")
            return

        # build filenames
        template_key = self.filename_template_combo.currentData()
        template_raw = (
            self.custom_filename_template.text().strip()
            if template_key == "custom"
            else template_key
        )

        preview_list = []
        for idx, piece in enumerate(pieces):
            fname = self._format_part_filename(piece, template_raw, Path(self.current_file).stem if self.current_file else "project", seq_index=idx + 1)
            preview_list.append({"piece": piece, "filename": fname})

        # Show dialog
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem

        dlg = QDialog(self)
        dlg.setWindowTitle("Filename Preview")
        dlg.resize(640, 480)
        layout = QVBoxLayout(dlg)

        table = QTableWidget(len(preview_list), 2)
        table.setHorizontalHeaderLabels(["Part Label/ID", "Filename (editable)"])
        for r, item in enumerate(preview_list):
            lbl = item["piece"].get("label", item["piece"].get("id", f"part_{r+1:03d}"))
            table.setItem(r, 0, QTableWidgetItem(str(lbl)))
            table.setItem(r, 1, QTableWidgetItem(str(item["filename"])))

        layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        def on_accept():
            # store resulting filenames in order
            self._export_filename_map = []
            for r in range(table.rowCount()):
                it = table.item(r, 1)
                self._export_filename_map.append(it.text().strip() if it else preview_list[r]["filename"])
            dlg.accept()

        buttons.accepted.connect(on_accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec_() == QDialog.Accepted:
            QMessageBox.information(self, "Preview Saved", "Filename preview saved and will be applied on export.")

    def load_file(self, file_path):
        try:
            self.current_file = file_path
            filename = os.path.basename(file_path)
            self.drop_area.label.setText(f"Loaded:\n{filename}")
            self.status_label.setText(f"Status: Ready [{filename}]")
            self.status_label.setStyleSheet(f"color: {self.theme['accent']}; font-weight: bold; font-size: 13px;")
            
            self.engine = SlicerEngine(file_path)
            self.planes = [] 
            self.refresh_slice_tab_status()
            
            dx, dy, dz = self.get_engine_bounds()
            self.dim_x.setText(f"{dx:.1f}")
            self.dim_y.setText(f"{dy:.1f}")
            self.dim_z.setText(f"{dz:.1f}")
            
            self.update_live_preview()
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error Loading File", f"Could not read 3D file:\n{str(e)}")

    def on_size_changed(self, changed_axis):
        if not self.engine:
            return
        try:
            if changed_axis == 'x':
                val = float(self.dim_x.text())
            elif changed_axis == 'y':
                val = float(self.dim_y.text())
            else:
                val = float(self.dim_z.text())

            if hasattr(self.engine, 'set_scale_by_dimension'):
                self.engine.set_scale_by_dimension(val, axis=changed_axis, uniform=self.lock_aspect.isChecked())

            dx, dy, dz = self.get_engine_bounds()
            self.dim_x.setText(f"{dx:.1f}")
            self.dim_y.setText(f"{dy:.1f}")
            self.dim_z.setText(f"{dz:.1f}")
            
            self.update_live_preview()
        except Exception as e:
            print(f"Sizing error: {e}")

    def trimesh_to_pyvista(self, mesh):
        vertices = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int64)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise ValueError("Mesh vertices are not an N x 3 array.")
        if faces.ndim != 2 or faces.shape[1] != 3:
            raise ValueError("The viewer requires triangular mesh faces.")
        vtk_faces = np.column_stack((
            np.full(len(faces), 3, dtype=np.int64), faces
        )).ravel()
        return pv.PolyData(vertices, vtk_faces)

    def get_axis_color(self, axis_idx):
        theme = self.theme
        colors = [
            theme.get("preview_primary", theme.get("accent", "#ff2a6d")),
            theme.get("preview_secondary", theme.get("accent_soft", "#00f0ff")),
            theme.get("preview_current_step", "#00ff9d"),
        ]
        return colors[axis_idx] if 0 <= axis_idx <= 2 else "#ffffff"

    def generate_preview_planes(self):
        new_planes = []

        if not self.engine or not hasattr(self.engine, "mesh"):
            return new_planes

        axis_idx = self.combo_axis.currentIndex()
        count = self.spin_count.value()
        spacing = self.spin_spacing.value()
        tilt_deg = self.slider_tilt.value()
        method = self.slice_method_combo.currentData() if hasattr(self, "slice_method_combo") else "count"

        family = self.combo_family.currentData()
        if family not in ("primary", "secondary", "custom"):
            family = "custom"

        base_normal = np.zeros(3, dtype=float)
        base_normal[axis_idx] = 1.0

        rot_axis = np.zeros(3, dtype=float)
        rot_axis[(axis_idx + 1) % 3] = 1.0

        rotation = R.from_rotvec(
            np.radians(tilt_deg) * rot_axis
        )
        tilted_normal = rotation.apply(base_normal)

        normal_length = float(np.linalg.norm(tilted_normal))
        if normal_length <= 1e-12:
            return new_planes

        tilted_normal = tilted_normal / normal_length

        surface = self.trimesh_to_pyvista(self.engine.mesh)

        vertices = np.asarray(self.engine.mesh.vertices, dtype=float)
        projected = vertices @ tilted_normal
        low, high = float(projected.min()), float(projected.max())
        span = max(0.0, high - low)

        if span <= 1e-9:
            return new_planes

        positions = []
        if method == "spacing":
            step = max(0.1, float(spacing))
            pos = low + step * 0.5
            while pos < high:
                positions.append(pos)
                pos += step
        elif method == "thickness":
            try:
                step = float(self.mat_thick_input.text().strip())
            except Exception:
                step = 3.0
            step = max(0.1, step)
            pos = low + step * 0.5
            while pos < high:
                positions.append(pos)
                pos += step
        else:
            count = max(1, int(count))
            positions = list(np.linspace(low, high, count + 2)[1:-1])

        if not positions:
            # Fallback to one middle slice so user always gets feedback.
            positions = [0.5 * (low + high)]

        color = self.get_axis_color(axis_idx)
        axis_name = ["X", "Y", "Z"][axis_idx]

        existing_family_count = sum(
            1 for plane in self.planes
            if plane.get("family", plane.get("axis")) == family
        )

        prefix = {
            "primary": "PRI",
            "secondary": "SEC",
            "custom": "CUS",
        }[family]

        center = np.asarray(self.engine.get_center(), dtype=float) if hasattr(self.engine, "get_center") else np.asarray(surface.center, dtype=float)
        center_proj = float(center @ tilted_normal)

        for index, pos in enumerate(positions):
            origin = center + (float(pos) - center_proj) * tilted_normal
            part_number = existing_family_count + index + 1

            new_planes.append({
                "id": f"{family}-{part_number:04d}",
                "label": f"{prefix}-{part_number:03d}",
                "origin": origin.tolist(),
                "normal": tilted_normal.tolist(),
                "color": color,
                "axis": family,
                "family": family,
                "display_axis": axis_name,
                "enabled": True,
                "order": len(self.planes) + index + 1,
            })

        return new_planes

    def _plane_signature(self, plane):
        normal = np.asarray(plane.get("normal", [0.0, 0.0, 1.0]), dtype=float)
        origin = np.asarray(plane.get("origin", [0.0, 0.0, 0.0]), dtype=float)
        return (
            str(plane.get("family", plane.get("axis", "custom"))),
            str(plane.get("display_axis", "")),
            tuple(np.round(normal, 6)),
            tuple(np.round(origin, 6)),
        )

    def _has_matching_saved_planes(self, preview_planes):
        if not preview_planes:
            return False
        if not self.planes:
            return False

        preview_signature = [self._plane_signature(plane) for plane in preview_planes]
        existing_signature = [self._plane_signature(plane) for plane in self.planes]
        return preview_signature == existing_signature[-len(preview_signature):]

    def refresh_slice_tab_status(self):
        if not hasattr(self, "saved_planes_label"):
            return

        if not self.planes:
            self.saved_planes_label.setText("Saved Planes: none")
            self.step_indicator.setText("Setup Ready · Save a plane batch to begin")
            return

        family_counts = {"primary": 0, "secondary": 0, "custom": 0}
        preview_lines = []
        for plane in self.planes:
            family = str(plane.get("family", plane.get("axis", "custom"))).lower()
            if family not in family_counts:
                family_counts["custom"] += 1
            else:
                family_counts[family] += 1
            if len(preview_lines) < 12:
                preview_lines.append(f"{plane.get('label', 'PLN')} · {plane.get('display_axis', '')} · {family}")

        summary = (
            f"Saved Planes: {len(self.planes)} "
            f"(Primary {family_counts['primary']} / Secondary {family_counts['secondary']} / Custom {family_counts['custom']})"
        )
        if preview_lines:
            summary += "\n" + "\n".join(preview_lines)
            if len(self.planes) > len(preview_lines):
                summary += f"\n... +{len(self.planes) - len(preview_lines)} more"

        self.saved_planes_label.setText(summary)
        self.step_indicator.setText("Planes Saved · Preview slices or build assembly steps")

    def commit_current_planes(self):
        if not self.engine or not hasattr(self.engine, 'mesh'):
            return

        preview = self.generate_preview_planes()
        if not preview:
            return

        if self._has_matching_saved_planes(preview):
            axis_name = ["X", "Y", "Z"][self.combo_axis.currentIndex()]
            self.btn_commit_planes.setText(f"↺ Already Saved {len(preview)} Planes for {axis_name}")
            QTimer.singleShot(1500, lambda: self.btn_commit_planes.setText("Save Plane Batch"))
            self.refresh_slice_tab_status()
            self.update_live_preview()
            return

        self.planes.extend(preview)

        axis_name = ["X", "Y", "Z"][self.combo_axis.currentIndex()]
        self.btn_commit_planes.setText(f"✓ Saved {len(preview)} Planes for {axis_name}")
        QTimer.singleShot(1500, lambda: self.btn_commit_planes.setText("Save Plane Batch"))

        self.refresh_slice_tab_status()
        self.update_live_preview()

    def clear_all_planes(self):
        self.planes = []
        self.refresh_slice_tab_status()
        self.update_live_preview()

    def update_live_preview(self):
        if not self.engine or not hasattr(self.engine, 'mesh'):
            return

        try:
            preview_pieces = self.build_parts()
        except Exception:
            preview_pieces = []

        self._render_preview_scene(preview_pieces)

    def _render_preview_scene(self, pieces):
        self.plotter.clear()
        apply_viewport_background(self.plotter, self.theme_key, use_image=False)
        self.plotter.add_axes()
        theme = self.theme

        if not pieces:
            surface = self.trimesh_to_pyvista(self.engine.mesh)
            self.plotter.add_mesh(surface, color=theme.get("preview_mesh", "#64748b"), opacity=0.18, show_edges=False)
            self.plotter.render()
            return

        self._render_preview_pieces(pieces)
        self.plotter.reset_camera()
        self.plotter.render()

    def _render_preview_pieces(self, pieces, batch_size=8):
        if not pieces:
            self.plotter.render()
            return

        theme = self.theme
        family_colors = {
            "primary": theme.get("preview_primary", "#ff2a6d"),
            "secondary": theme.get("preview_secondary", "#00f0ff"),
            "custom": theme.get("preview_custom", "#ffae00"),
        }

        self._preview_piece_index = 0
        self._preview_piece_batch = pieces
        self._preview_piece_batch_size = batch_size
        self._preview_piece_colors = family_colors

        self._render_preview_batch()

    def _render_preview_batch(self):
        if not hasattr(self, "_preview_piece_batch"):
            return

        start = self._preview_piece_index
        end = min(start + self._preview_piece_batch_size, len(self._preview_piece_batch))

        for offset in range(start, end):
            piece = self._preview_piece_batch[offset]
            family = piece.get("family", piece.get("axis", "custom"))
            color = self._preview_piece_colors.get(family, "#ffffff")
            self._add_profile_to_plotter(piece, color, opacity=0.9, show_edges=False)

        self._preview_piece_index = end
        self.plotter.render()

        if self._preview_piece_index < len(self._preview_piece_batch):
            QTimer.singleShot(0, self._render_preview_batch)

    def _get_preview_transform(self, piece):
        transform = np.asarray(piece.get("tf", np.eye(4)), dtype=float).copy()

        try:
            normal = np.asarray(piece.get("normal", [0.0, 0.0, 1.0]), dtype=float)
            normal_norm = float(np.linalg.norm(normal))
            if normal_norm > 1e-12:
                normal = normal / normal_norm
                family = str(piece.get("family", piece.get("axis", "custom")))
                family_planes = [
                    plane for plane in self.planes
                    if str(plane.get("family", plane.get("axis", "custom"))) == family
                ]

                if family_planes:
                    family_planes = sorted(
                        family_planes,
                        key=lambda plane: int(plane.get("order", 0)),
                    )
                    plane_key = piece.get("id") or piece.get("label")
                    plane_index = None
                    for index, plane in enumerate(family_planes):
                        if plane.get("id") == plane_key or plane.get("label") == plane_key:
                            plane_index = index
                            break

                    if plane_index is None:
                        plane_index = min(len(family_planes) - 1, max(0, int(piece.get("order", 0)) - 1))

                    if plane_index > 0:
                        prev_plane = family_planes[plane_index - 1]
                        prev_origin = np.asarray(prev_plane.get("origin", [0.0, 0.0, 0.0]), dtype=float)
                        curr_origin = np.asarray(family_planes[plane_index].get("origin", [0.0, 0.0, 0.0]), dtype=float)
                        spacing = abs(float(np.dot(curr_origin - prev_origin, normal)))
                    else:
                        spacing = 0.0

                    stack_offset = spacing * max(0, plane_index)
                    transform[:3, 3] = transform[:3, 3] + normal * stack_offset
                else:
                    material_thickness = float(self.mat_thick_input.text().strip()) if self.mat_thick_input.text().strip() else 3.0
                    kerf = float(self.kerf_input.text().strip()) if self.kerf_input.text().strip() else 0.15
                    plane_spacing = float(self.spin_spacing.value()) if hasattr(self, "spin_spacing") else 0.0
                    stack_step = max(plane_spacing if plane_spacing > 0 else material_thickness, material_thickness + kerf)
                    order = max(0, int(piece.get("order", 0)) - 1)
                    transform[:3, 3] = transform[:3, 3] + normal * (order * stack_step)
        except Exception:
            pass

        return transform

    def _add_profile_to_plotter(self, piece, color, opacity=0.90, show_edges=False):
        transform = self._get_preview_transform(piece)
        thickness = float(self.mat_thick_input.text().strip()) if self.mat_thick_input.text().strip() else 3.0

        # Prefer engine-provided slice geometry when available to avoid
        # duplicated center-section artifacts from heuristic clipping. The
        # engine mesh is already transformed into model/world coordinates.
        try:
            piece_mesh = piece.get("mesh")
            if piece_mesh is not None and hasattr(piece_mesh, "vertices") and piece_mesh.vertices is not None:
                mesh_copy = piece_mesh.copy()
                self.plotter.add_mesh(mesh_copy, color=color, opacity=opacity, show_edges=show_edges)
                if piece.get("slotted", False):
                    edge_color = self.theme.get("preview_slot", "#ffd166")
                    self.plotter.add_mesh(mesh_copy, color=edge_color, style="wireframe", line_width=2, opacity=0.95)
                return
        except Exception:
            pass

        try:
            mesh = self.engine.mesh
            extents = np.asarray(mesh.extents, dtype=float)
            padding = max(2.0, float(np.max(extents)) * 0.15)
            slab_size = np.array([
                max(extents[0] + padding, thickness * 2.0),
                max(extents[1] + padding, thickness * 2.0),
                max(thickness, 0.5),
            ], dtype=float)

            slab = trimesh.creation.box(extents=slab_size, transform=np.eye(4))
            slab.apply_transform(transform)
            clipped_piece = trimesh.boolean.intersection([mesh, slab], engine="scad")

            if clipped_piece is not None and hasattr(clipped_piece, "vertices") and clipped_piece.vertices.size > 0:
                self.plotter.add_mesh(clipped_piece, color=color, opacity=opacity, show_edges=show_edges)
                if piece.get("slotted", False):
                    edge_color = self.theme.get("preview_slot", "#ffd166")
                    self.plotter.add_mesh(clipped_piece, color=edge_color, style="wireframe", line_width=2, opacity=0.95)
                return
        except Exception:
            pass

        geom = piece["poly"]
        polygons = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)

        for polygon in polygons:
            if polygon.is_empty:
                continue

            exterior = sg.Polygon(list(polygon.exterior.coords))
            try:
                base_mesh = trimesh.creation.extrude_polygon(exterior, height=thickness)
            except Exception:
                continue

            for interior in polygon.interiors:
                hole = sg.Polygon(list(interior.coords))
                try:
                    hole_mesh = trimesh.creation.extrude_polygon(hole, height=thickness)
                    base_mesh = trimesh.boolean.difference([base_mesh, hole_mesh], engine="scad")
                except Exception:
                    continue

            try:
                base_mesh.apply_transform(transform)
            except Exception:
                pass

            if base_mesh is not None:
                self.plotter.add_mesh(base_mesh, color=color, opacity=opacity, show_edges=show_edges)
                if piece.get("slotted", False):
                    edge_color = self.theme.get("preview_slot", "#ffd166")
                    self.plotter.add_mesh(base_mesh, color=edge_color, style="wireframe", line_width=2, opacity=0.95)

    def generate_assembly_steps_preview(self):
        if not self.engine or not self.planes:
            QMessageBox.warning(self, "No Planes", "Load a model and save at least one cut-plane group.")
            return

        try:
            pieces = self.build_parts()
        except Exception as exc:
            QMessageBox.critical(self, "Slicing Error", str(exc))
            return

        ordered_pieces = sorted(
            pieces,
            key=lambda piece: (
                int(piece.get("order", 0)),
                str(piece.get("label", "")),
            ),
        )

        mode = self.assembly_engine_combo.currentData() if hasattr(self, "assembly_engine_combo") else "interlocking"
        self.assembly_plan_mode = mode
        self.assembly_steps = ordered_pieces
        self._update_assembly_validation(ordered_pieces)

        if mode == "welded":
            polygon_parts = []
            for piece in ordered_pieces:
                weld_edges = []
                poly = piece.get("poly")
                if poly is not None and hasattr(poly, "exterior") and poly.exterior is not None:
                    coords = list(poly.exterior.coords)
                    for edge_index in range(max(0, len(coords) - 1)):
                        p0 = coords[edge_index]
                        p1 = coords[edge_index + 1]
                        weld_edges.append({
                            "edge": edge_index + 1,
                            "from": [float(p0[0]), float(p0[1])],
                            "to": [float(p1[0]), float(p1[1])],
                        })

                polygon_parts.append({
                    "id": piece.get("id", piece.get("label", "part")),
                    "label": piece.get("label", ""),
                    "order": piece.get("order", 0),
                    "weld_edges": weld_edges,
                })

            self.assembly_plan = self.engine.generate_welded_polygon_assembly_steps(polygon_parts)
        else:
            self.assembly_plan = self.engine.generate_interlocking_assembly_steps(ordered_pieces)

        if not self.assembly_steps or not self.assembly_plan:
            self.assembly_steps_slider.setRange(0, 0)
            self.assembly_steps_slider.setEnabled(False)
            self.assembly_steps_label.setText("Assembly step: 0 / 0")
            return

        self.assembly_steps_slider.setRange(0, len(self.assembly_plan))
        self.assembly_steps_slider.setValue(len(self.assembly_plan))
        self.assembly_steps_slider.setEnabled(True)
        self.update_assembly_step_preview(len(self.assembly_plan))

    def update_assembly_step_preview(self, value):
        if not self.assembly_steps:
            return

        total_steps = len(self.assembly_plan) if self.assembly_plan else len(self.assembly_steps)
        step_count = max(0, min(int(value), total_steps))
        self.assembly_step_index = step_count

        piece_count = sum(
            1 for step in self.assembly_plan[:step_count]
            if step.get("type") in {"insert_interlock", "fit_up"}
        )
        piece_count = max(0, min(piece_count, len(self.assembly_steps)))

        step_label = f"Assembly step: {step_count} / {total_steps}"
        if self.assembly_plan and step_count > 0:
            active = self.assembly_plan[step_count - 1]
            active_name = str(active.get("type", "step")).replace("_", " ").title()
            active_part = active.get("label", active.get("piece_id", active.get("part_id", "")))
            if active_part:
                step_label += f" · {active_name} · {active_part}"
            else:
                step_label += f" · {active_name}"
        self.assembly_steps_label.setText(step_label)

        self.plotter.clear()
        apply_viewport_background(self.plotter, self.theme_key, use_image=False)
        self.plotter.add_axes()

        if not self.engine or not hasattr(self.engine, 'mesh'):
            return

        theme = self.theme
        surface = self.trimesh_to_pyvista(self.engine.mesh)
        self.plotter.add_mesh(surface, color=theme.get("preview_mesh", "#64748b"), opacity=0.16, show_edges=False)

        invalid_labels = set(getattr(self, "_assembly_invalid_labels", ()))
        for index, piece in enumerate(self.assembly_steps[:piece_count], start=1):
            color = "#ffffff"
            if piece.get("label", piece.get("id", "")) in invalid_labels:
                color = "#ff4d4d"
            elif index == piece_count and piece_count > 0 and piece_count < len(self.assembly_steps):
                color = theme.get("preview_current_step", "#00ff9d")
            elif piece.get("slotted", False):
                color = theme.get("preview_slot", "#ffd166")
            else:
                family = piece.get("family", piece.get("axis", "custom"))
                color = {
                    "primary": theme.get("preview_primary", "#ff2a6d"),
                    "secondary": theme.get("preview_secondary", "#00f0ff"),
                    "custom": theme.get("preview_custom", "#ffae00"),
                }.get(family, "#ffffff")

            self._add_profile_to_plotter(piece, color, opacity=0.92 if index == step_count else 0.78, show_edges=False)

        self.plotter.reset_camera()
        self.plotter.render()

    def _update_assembly_validation(self, pieces):
        self._assembly_invalid_labels = set()
        if self.assembly_plan_mode != "interlocking":
            self.assembly_validation_label.setText("Physical fit: welding sequence; inspect fit-up and edge access")
            self.assembly_validation_label.setStyleSheet(f"color: {self.theme['accent']}; font-weight: bold;")
            return

        try:
            thickness = float(self.mat_thick_input.text().strip())
            clearance = float(self.fit_clearance.value())
            issues = self.engine.validate_interlocking_assembly(pieces, thickness, clearance)
        except Exception as exc:
            issues = [{"severity": "error", "message": f"Physical fit check failed: {exc}"}]

        self._assembly_invalid_labels = set()
        for issue in issues:
            labels = issue.get("labels") or ([issue.get("label")] if issue.get("label") else [])
            self._assembly_invalid_labels.update(str(label) for label in labels if label)
        if not issues:
            self.assembly_validation_label.setText("Physical fit: PASS")
            self.assembly_validation_label.setStyleSheet("color: #00c853; font-weight: bold;")
            return

        first_messages = [str(issue.get("message", "Assembly issue")) for issue in issues[:3]]
        suffix = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
        self.assembly_validation_label.setText("Physical fit: BLOCKED\n" + "\n".join(first_messages) + suffix)
        self.assembly_validation_label.setStyleSheet("color: #ff4d4d; font-weight: bold;")

    def build_parts(self):
        if not self.engine:
            raise ValueError("No 3D model is loaded.")

        enabled_planes = [
            plane for plane in self.planes
            if plane.get("enabled", True)
        ]

        if not enabled_planes:
            raise ValueError("No enabled cut planes have been saved.")

        try:
            material_thickness = float(
                self.mat_thick_input.text().strip()
            )
            kerf = float(
                self.kerf_input.text().strip()
            )
        except ValueError as exc:
            raise ValueError(
                "Material thickness and kerf must be valid numbers."
            ) from exc

        if material_thickness <= 0:
            raise ValueError(
                "Material thickness must be greater than zero."
            )

        if kerf < 0:
            raise ValueError("Kerf cannot be negative.")

        pieces = self.engine.slice_at_planes(
            enabled_planes,
            kerf=kerf,
            material_thickness=material_thickness,
        )

        slot_config = {
            "enabled": self.enable_notches.isChecked(),
            "shape": self.notch_shape.currentData(),
            "count": self.notch_count.value(),
            "width": self.notch_width.value(),
            "depth": self.notch_depth.value(),
            "clearance": self.fit_clearance.value(),
            "target": "both",
            "material_thickness": material_thickness,
            "dovetail_ratio": 0.60,
            "tslot_head": 1.60,
            "corner_radius": 0.40,
        }

        pieces = self.engine.apply_slots(
            pieces,
            slot_config,
        )

        if not pieces:
            raise ValueError(
                "No valid slice parts were generated."
            )

        for index, piece in enumerate(pieces, start=1):
            piece["order"] = index

        return pieces

    def preview_final_slices(self):
        if not self.engine or not self.planes:
            QMessageBox.warning(
                self,
                "No Planes",
                "Load a model and save at least one cut-plane group.",
            )
            return

        try:
            pieces = self.build_parts()
            self._render_preview_scene(pieces)
            self.plotter.reset_camera()
            self.plotter.render()

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Slicing Error",
                str(exc),
            )

    def process_and_export(self):
        if not self.engine:
            QMessageBox.warning(
                self,
                "Nothing to Export",
                "Load a model first.",
            )
            return

        if not any((
            self.export_nested_dxf.isChecked(),
            self.export_individual_dxf.isChecked(),
            self.export_3d_step.isChecked(),
            self.export_nested_3d_step.isChecked(),
            self.export_flat_pattern_dxf.isChecked(),
            self.export_solid_bend_package.isChecked(),
        )):
            QMessageBox.warning(
                self,
                "No Export Mode",
                "Select at least one export option.",
            )
            return

        needs_slice_exports = any((
            self.export_nested_dxf.isChecked(),
            self.export_individual_dxf.isChecked(),
            self.export_3d_step.isChecked(),
            self.export_nested_3d_step.isChecked(),
        ))

        if needs_slice_exports and not self.planes:
            QMessageBox.warning(
                self,
                "Nothing to Export",
                "Save at least one cut-plane group for slice-based exports.",
            )
            return

        output_directory = QFileDialog.getExistingDirectory(
            self,
            "Select Export Folder",
            "",
        )

        if not output_directory:
            return

        try:
            from pathlib import Path

            output_root = Path(output_directory)

            project_name = (
                Path(self.current_file).stem
                if self.current_file
                else "slicer_project"
            )

            project_name = self.engine._safe_filename(
                project_name
            )

            project_directory = (
                output_root /
                f"{project_name}_Export"
            )

            parts_directory = (
                project_directory /
                "parts"
            )

            sheets_directory = (
                project_directory /
                "sheets"
            )

            step_directory = (
                project_directory /
                "step"
            )

            project_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            material_thickness = float(
                self.mat_thick_input.text().strip()
            )

            sheet_width = float(
                self.sheet_w.value()
            )

            sheet_height = float(
                self.sheet_h.value()
            )

            pieces = self.build_parts() if needs_slice_exports else []

            nested_sheets = None
            final_width = None
            final_height = None

            needs_nesting = (
                self.export_nested_dxf.isChecked()
                or
                self.export_nested_3d_step.isChecked()
            )

            if needs_nesting:
                nested_sheets, final_width, final_height = (
                    self.engine.nest_pieces(
                        pieces,
                        sheet_width=sheet_width,
                        sheet_height=sheet_height,
                    )
                )

            exported_items = []

            if self.export_individual_dxf.isChecked():
                parts_directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                # Prepare template/map
                template_key = self.filename_template_combo.currentData()
                template_raw = (
                    self.custom_filename_template.text().strip()
                    if template_key == "custom"
                    else template_key
                )

                preview_map = getattr(self, "_export_filename_map", None)

                # Try engine-side export with template or filename_map if supported
                individual_files = None
                try:
                    # try filename_map kw first
                    if preview_map:
                        try:
                            individual_files = self.engine.export_individual_dxfs(
                                pieces,
                                parts_directory,
                                filename_map=preview_map,
                            )
                        except TypeError:
                            individual_files = None

                    # try template kw next
                    if individual_files is None and template_raw:
                        try:
                            individual_files = self.engine.export_individual_dxfs(
                                pieces,
                                parts_directory,
                                filename_template=template_raw,
                            )
                        except TypeError:
                            individual_files = None

                    # call without extras as fallback
                    if individual_files is None:
                        individual_files = self.engine.export_individual_dxfs(
                            pieces,
                            parts_directory,
                        )
                except Exception:
                    # engine export failed; ensure we have something to work with
                    try:
                        individual_files = self.engine.export_individual_dxfs(pieces, parts_directory)
                    except Exception:
                        individual_files = []
                # Optionally rename individual files according to template
                try:
                    template_key = self.filename_template_combo.currentData()
                    template_raw = (
                        self.custom_filename_template.text().strip()
                        if template_key == "custom"
                        else template_key
                    )

                    from pathlib import Path
                    import shutil

                    renamed = []

                    # Pair by index when possible
                    for idx, out_path in enumerate(individual_files):
                        try:
                            piece = pieces[idx] if idx < len(pieces) else {}
                            if preview_map and idx < len(preview_map):
                                new_name = preview_map[idx]
                            else:
                                new_name = self._format_part_filename(
                                    piece, template_raw, project_name, seq_index=idx + 1
                                )
                            src = Path(out_path)
                            if not src.exists():
                                # file missing, skip
                                renamed.append(str(out_path))
                                continue
                            dst = src.with_name(new_name + src.suffix)
                            # avoid overwriting
                            if dst.exists():
                                # append index
                                dst = src.with_name(f"{new_name}_{idx+1:03d}" + src.suffix)
                            shutil.move(str(src), str(dst))
                            renamed.append(str(dst))
                        except Exception:
                            renamed.append(str(out_path))

                    individual_files = renamed

                except Exception:
                    pass

                exported_items.append(
                    f"{len(individual_files)} individual DXF parts"
                )

            if self.export_nested_dxf.isChecked():
                sheets_directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                nested_dxf_path = (
                    sheets_directory /
                    f"{project_name}_nested.dxf"
                )

                self.engine.export_nested_to_dxf(
                    nested_sheets,
                    final_width,
                    final_height,
                    output_filename=nested_dxf_path,
                )

                exported_items.append(
                    "nested fabrication DXF"
                )

            if self.export_3d_step.isChecked():
                step_directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                self.engine.export_assembly_to_step(
                    pieces,
                    step_directory,
                    material_thickness,
                )

                exported_items.append(
                    "individual STEP files + assembled STEP"
                )

            if self.export_nested_3d_step.isChecked():
                step_directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                nested_step_directory = (
                    step_directory / "nested"
                )

                self.engine.export_nested_parts_to_step(
                    nested_sheets,
                    nested_step_directory,
                    material_thickness,
                )

                exported_items.append(
                    "nested STEP files"
                )

            if self.export_flat_pattern_dxf.isChecked():
                sheets_directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                flat_pattern_path = (
                    sheets_directory /
                    f"{project_name}_flat_pattern.dxf"
                )

                self.engine.export_flat_pattern_with_bend_lines(
                    flat_pattern_path,
                    bend_min_angle_deg=1.0,
                    component_gap=25.0,
                    margin=10.0,
                )

                exported_items.append(
                    "flat pattern DXF (CUT + BEND layers)"
                )

            if self.export_solid_bend_package.isChecked():
                bend_directory = (
                    project_directory /
                    "bend_model"
                )
                bend_directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                bend_package = self.engine.export_solid_with_bend_sketch_package(
                    output_directory=bend_directory,
                    base_name=project_name,
                    bend_min_angle_deg=1.0,
                    component_gap=25.0,
                    margin=10.0,
                    try_export_ipt=True,
                )

                exported_items.append(
                    "solid bend package (solid + bend sketch DXF)"
                )
                if bend_package.get("inventor_ipt"):
                    exported_items.append("Inventor IPT bend model")
                else:
                    exported_items.append(
                        "Inventor IPT attempt unavailable (see status in terminal/log)"
                    )

            QMessageBox.information(
                self,
                "Export Complete",
                "Exported:\n\n- "
                + "\n- ".join(exported_items)
                + "\n\nFolder:\n"
                + str(project_directory),
            )

        except Exception as exc:
            import traceback

            traceback.print_exc()

            QMessageBox.critical(
                self,
                "Export Error",
                str(exc),
            )


if __name__ == "__main__":
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 11))

    main_window = None

    def launch_main():
        global main_window
        main_window = SlicerApp()
        screen = app.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            width = min(1600, int(available.width() * 0.85))
            height = min(980, int(available.height() * 0.86))
            main_window.resize(width, height)
            frame = main_window.frameGeometry()
            frame.moveCenter(available.center())
            main_window.move(frame.topLeft())
        main_window.show()

    boot = BootScreen(launch_main)
    boot.show()
    sys.exit(app.exec_())
