import os
import sys
import math
import random
import time
import threading
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
import pygame
import joblib

# Compatibility for PPO checkpoints saved with NumPy 2.x when this game
# is running in an environment that still has NumPy 1.x (e.g. MediaPipe).
# Older NumPy releases expose these modules under numpy.core instead.
try:
    import numpy.core as _numpy_core
    import numpy.core.numeric as _numpy_numeric
    sys.modules.setdefault("numpy._core", _numpy_core)
    sys.modules.setdefault("numpy._core.numeric", _numpy_numeric)
except Exception:
    pass

from stable_baselines3 import PPO

# ============================================================
# AI SUPERPOWERS  //  G.ONE vs RA.ONE  --  CINEMATIC EDITION
#
# Keeps: MediaPipe hand tracking, the trained RandomForest at
#        models/gesture_model.pkl, extract_features(), the gesture ->
#        action pipeline and all gameplay numbers.
# Uses : characters/gone.png (player) and characters/raone.png (enemy).
# No network access or remote assets are used at runtime.
# ============================================================

CHARACTER_DIR = "characters"
FLIP_RAONE = True          # set False if raone.png already faces left in the file

WIDTH, HEIGHT = 1200, 760
FPS = 60
MODEL_PATH = os.path.join("models", "gesture_model.pkl")
CAMERA_INDEX = 0
USE_CAMERA_THREAD = True   # False = original synchronous camera read in the main loop

# ---------------- Gameplay tuning (unchanged) ----------------
PLAYER_MAX_HP = 300
ENEMY_MAX_HP = 360
ENEMY_ATTACK_MIN = 0.55
ENEMY_ATTACK_MAX = 0.85
GESTURE_CONFIDENCE = 0.58
STABLE_FRAMES = 2
ACTION_COOLDOWN = 0.25

ATTACK_DAMAGE = 12
SPECIAL_DAMAGE = 25
HEAL_AMOUNT = 18
ENEMY_DAMAGE = 12
SHIELD_DURATION = 1.5
ROUND_SECONDS = 60  # Default / Round 1
ROUND_DURATIONS = {1: 90, 2: 80, 3: 70}
PLAYER_ROUND_HP = {1: 360, 2: 340, 3: 320}
ENEMY_ROUND_HP = {1: 420, 2: 450, 3: 480}
# Keep later rounds challenging without making the villain unnaturally frantic.
ROUND_PRESSURE = {1: 1.0, 2: 0.96, 3: 0.92}

# ---------------- Presentation tuning ----------------
ATTACK_ANIM = 0.34
SPECIAL_ANIM = 0.95
SPECIAL_CHARGE = 0.50
HEAL_ANIM = 1.10
COMBO_WINDOW = 2.4
KO_DELAY = 1.35
COUNTDOWN_STEP = 0.8

HORIZON_Y = 470
HUD_TOP = 112
DOCK_Y = 650
GROUND_Y = 630
PLAYER_X = 290
ENEMY_X = 910
PARALLAX_PAD = 40
HALO_PAD = 44
BREATH_FRAMES = 20
MAX_PARTICLES = 520
MAX_FLOATING = 28
MAX_PROJECTILES = 8

COLORS = {
    "bg": (7, 9, 24),
    "panel": (12, 18, 39),
    "cyan": (0, 238, 255),
    "blue": (40, 110, 255),
    "blue_bright": (90, 170, 255),
    "pink": (255, 40, 170),
    "red": (255, 54, 83),
    "purple": (154, 80, 255),
    "violet": (170, 100, 255),
    "green": (50, 255, 170),
    "yellow": (255, 220, 80),
    "white": (230, 246, 255),
    "muted": (119, 151, 185),
    "ember": (255, 140, 60),
}

ACTION_LABELS = {
    "attack": "ATTACK",
    "shield": "SHIELD",
    "heal": "HEAL",
    "special": "SPECIAL",
}
ACTION_COLORS = {
    "attack": COLORS["blue_bright"],
    "shield": COLORS["cyan"],
    "heal": COLORS["green"],
    "special": COLORS["violet"],
}
GESTURE_NAMES = {"attack": "FIST", "shield": "PALM", "heal": "LIKE", "special": "PEACE"}
GESTURE_ICONS = {"attack": "F", "shield": "S", "heal": "+", "special": "V"}
ACTION_DETAIL = {
    "attack": f"{ATTACK_DAMAGE} DMG",
    "shield": f"{SHIELD_DURATION:.0f} SEC",
    "heal": f"+{HEAL_AMOUNT} HP",
    "special": f"{SPECIAL_DAMAGE} DMG",
}
ACTION_ORDER = ["attack", "shield", "heal", "special"]


def extract_features(hand_landmarks):
    """Must match the feature preprocessing used by train_model.py."""
    pts = np.array([[lm.x, lm.y] for lm in hand_landmarks.landmark], dtype=np.float32)
    pts -= pts[0]  # wrist-relative
    scale = float(np.max(np.linalg.norm(pts, axis=1)))
    if scale > 1e-6:
        pts /= scale
    return pts.flatten().reshape(1, -1)


# ============================================================
# Small helpers
# ============================================================
def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def lerp(a, b, t):
    return a + (b - a) * t


def ease_out_cubic(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_out_back(t):
    c1 = 1.70158
    c3 = c1 + 1
    t = clamp(t)
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def scale_color(c, k):
    return tuple(int(clamp(v * k, 0, 255)) for v in c[:3])


def mix_color(a, b, t):
    t = clamp(t)
    return tuple(int(lerp(a[i], b[i], t)) for i in range(3))


def side_color(u):
    """Cyan (G.ONE side) -> violet (centre) -> crimson (RA.ONE side)."""
    u = clamp(u)
    cyan, violet, red = (40, 200, 255), (150, 90, 255), (255, 60, 90)
    return mix_color(cyan, violet, u * 2) if u < 0.5 else mix_color(violet, red, (u - 0.5) * 2)


def safe_text(surface, font, text, color, pos):
    surface.blit(font.render(str(text), True, color), pos)


# ---------------- font / text caches ----------------
_FONT_CACHE = {}
_TEXT_CACHE = {}
_GLOW_CACHE = {}


def get_font(size):
    f = _FONT_CACHE.get(size)
    if f is None:
        f = pygame.font.Font(None, size)
        _FONT_CACHE[size] = f
    return f


def render_text(font, text, color):
    key = (id(font), text, color)
    img = _TEXT_CACHE.get(key)
    if img is None:
        if len(_TEXT_CACHE) > 700:
            _TEXT_CACHE.clear()
        img = font.render(text, True, color)
        _TEXT_CACHE[key] = img
    return img


def blit_text(surface, font, text, color, pos, anchor="topleft"):
    img = render_text(font, str(text), color)
    rect = img.get_rect(**{anchor: (int(pos[0]), int(pos[1]))})
    surface.blit(img, rect)
    return rect


def glow_text(font, text, color, glow_color=None, spread=8):
    """Neon text with a soft halo. Cached, so it is cheap to reuse every frame."""
    key = ("g", id(font), text, color, glow_color, spread)
    out = _TEXT_CACHE.get(key)
    if out is not None:
        return out
    if len(_TEXT_CACHE) > 700:
        _TEXT_CACHE.clear()
    glow_color = glow_color or color
    base = font.render(text, True, color)
    halo = font.render(text, True, glow_color)
    pad = spread * 2
    size = (base.get_width() + pad * 2, base.get_height() + pad * 2)
    canvas = pygame.Surface(size, pygame.SRCALPHA)
    canvas.fill((*glow_color[:3], 0))
    canvas.blit(halo, (pad, pad))
    small = pygame.transform.smoothscale(canvas, (max(1, size[0] // 4), max(1, size[1] // 4)))
    blur = pygame.transform.smoothscale(small, size)
    out = pygame.Surface(size, pygame.SRCALPHA)
    for _ in range(2):
        out.blit(blur, (0, 0))
    out.blit(base, (pad, pad))
    _TEXT_CACHE[key] = out
    return out


# ---------------- additive glow sprites ----------------
def _build_glow(radius, color, level):
    size = radius * 2
    ax = np.arange(size, dtype=np.float32) - radius + 0.5
    d = np.sqrt(ax[:, None] ** 2 + ax[None, :] ** 2) / radius
    k = np.clip(1.0 - d, 0, 1) ** 2.2 * level
    arr = np.clip(k[..., None] * np.array(color[:3], dtype=np.float32), 0, 255).astype(np.uint8)
    return pygame.surfarray.make_surface(arr).convert()


def get_glow(radius, color, level=1.0):
    radius = max(2, int(radius))
    q = 2 if radius < 24 else (6 if radius < 80 else 12)
    radius = max(2, int(round(radius / q)) * q)
    lv = max(0, min(8, int(round(level * 8))))
    if lv == 0:
        return None
    key = (radius, tuple(color[:3]), lv)
    s = _GLOW_CACHE.get(key)
    if s is None:
        if len(_GLOW_CACHE) > 900:
            _GLOW_CACHE.clear()
        s = _build_glow(radius, key[1], lv / 8.0)
        _GLOW_CACHE[key] = s
    return s


def draw_glow(surface, pos, radius, color, level=1.0):
    g = get_glow(radius, color, level)
    if g is None:
        return
    surface.blit(g, (int(pos[0] - g.get_width() / 2), int(pos[1] - g.get_height() / 2)),
                 special_flags=pygame.BLEND_RGB_ADD)


# ============================================================
# Character loading (alpha-aware, aspect-preserving)
# ============================================================
def load_character(filename, size=(350, 440)):
    """Load a PNG, crop transparent margins, fit inside `size` keeping its aspect ratio."""
    path = os.path.join(CHARACTER_DIR, filename)

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Character image not found: {path}\n"
            f"       Expected the file at <project root>/{CHARACTER_DIR}/{filename}. "
            f"Run the game from the project root."
        )
    try:
        image = pygame.image.load(path).convert_alpha()
    except pygame.error as exc:
        raise RuntimeError(f"Could not read character image {path}: {exc}") from exc

    alpha = pygame.surfarray.array_alpha(image)
    if int(alpha.min()) == 255:
        print(f"WARNING: {path} has no transparent pixels. It will be drawn as-is, "
              f"but a PNG with a transparent background will look much better.")
    bounds = image.get_bounding_rect()
    if bounds.width == 0 or bounds.height == 0:
        raise ValueError(f"Character image {path} is completely transparent.")
    image = image.subsurface(bounds).copy()          # trim empty margins only; pixels unchanged

    # Mirror only RA.ONE so the two fighters face each other.
    if FLIP_RAONE and filename.lower().startswith("raone"):
        image = pygame.transform.flip(image, True, False)

    w, h = image.get_size()
    s = min(size[0] / w, size[1] / h)                # uniform scale: never stretched
    new_size = (max(1, int(round(w * s))), max(1, int(round(h * s))))
    return pygame.transform.smoothscale(image, new_size)


# ============================================================
# Pre-rendered scene pieces
# ============================================================
def create_gradient_background(width, height):
    """Blue -> violet -> dark red night sky, generated once at startup."""
    blue, violet, red = np.array((8, 40, 120)), np.array((34, 12, 76)), np.array((110, 10, 38))
    xs = np.linspace(0, 1, width)
    cols = np.zeros((width, 3), dtype=np.float32)
    for i in range(3):
        cols[:, i] = np.where(xs < 0.5,
                              blue[i] + (violet[i] - blue[i]) * xs * 2,
                              violet[i] + (red[i] - violet[i]) * (xs - 0.5) * 2)
    ys = np.linspace(0, 1, height)
    horizon = HORIZON_Y / height
    vf = 0.30 + 0.70 * np.exp(-((ys - horizon) / 0.34) ** 2)
    arr = cols[:, None, :] * vf[None, :, None]
    return pygame.surfarray.make_surface(np.clip(arr, 0, 255).astype(np.uint8)).convert()


def build_skyline(seed, layer_h, min_h, max_h, width_range, body, edge_alpha, window_chance, bright):
    rng = random.Random(seed)
    total_w = WIDTH + 2 * PARALLAX_PAD
    surf = pygame.Surface((total_w, layer_h), pygame.SRCALPHA)
    x = 0
    while x < total_w:
        bw = rng.randint(*width_range)
        bh = rng.randint(min_h, max_h)
        top = layer_h - bh
        rect = pygame.Rect(x, top, bw, bh)
        tint = side_color((x + bw / 2) / total_w)
        pygame.draw.rect(surf, body, rect)
        pygame.draw.line(surf, (*tint, edge_alpha), rect.topleft, rect.topright, 2)
        pygame.draw.line(surf, (*tint, edge_alpha // 2), rect.topleft, rect.bottomleft, 1)
        style = rng.random()
        if style < 0.16:       # spire
            pygame.draw.polygon(surf, body, [(rect.centerx - bw // 4, top),
                                             (rect.centerx, top - rng.randint(30, 70)),
                                             (rect.centerx + bw // 4, top)])
            pygame.draw.circle(surf, (*tint, 255), (rect.centerx, top - 4), 2)
        elif style < 0.30:     # antenna
            ax = rect.x + rng.randint(6, max(7, bw - 6))
            pygame.draw.line(surf, (*tint, 160), (ax, top), (ax, top - rng.randint(14, 34)), 1)
            pygame.draw.circle(surf, (*mix_color(tint, (255, 255, 255), 0.4), 255), (ax, top - 20), 2)
        for wy in range(top + 10, layer_h - 6, 14):
            for wx in range(x + 6, x + bw - 6, 10):
                if rng.random() < window_chance:
                    c = mix_color(tint, (255, 255, 255), rng.random() * 0.4)
                    pygame.draw.rect(surf, (*scale_color(c, bright), 255), (wx, wy, 3, 5))
        if rng.random() < 0.25:   # vertical neon strip
            sx = x + rng.randint(3, max(4, bw - 4))
            pygame.draw.line(surf, (*tint, 150), (sx, top + 6), (sx, layer_h - 4), 1)
        x += bw + rng.randint(-6, 4)
    return surf


def make_beam(length, top_w, bottom_w, color, peak_alpha):
    surf = pygame.Surface((top_w, length), pygame.SRCALPHA)
    cx = top_w / 2
    pygame.draw.polygon(surf, (*color, 255), [(0, 0), (top_w, 0), (cx + bottom_w / 2, length), (cx - bottom_w / 2, length)])
    al = pygame.surfarray.pixels_alpha(surf)
    grad = (np.linspace(1, 0, length) ** 1.4)[None, :]
    al[:] = (al.astype(np.float32) * grad * (peak_alpha / 255.0)).astype(np.uint8)
    del al
    return surf


def paint_floor(bg):
    fh = HEIGHT - HORIZON_Y
    xs = np.linspace(0, 1, WIDTH)
    left, mid, right = np.array((10, 40, 110)), np.array((30, 14, 70)), np.array((100, 10, 40))
    base = np.zeros((WIDTH, 1, 3), dtype=np.float32)
    for i in range(3):
        base[:, 0, i] = np.where(xs < 0.5, left[i] + (mid[i] - left[i]) * xs * 2,
                                 mid[i] + (right[i] - mid[i]) * (xs - 0.5) * 2)
    ys = np.linspace(0, 1, fh)
    inten = (0.55 * (1 - ys) ** 1.6 + 0.12)[None, :, None]
    floor = pygame.surfarray.make_surface(np.clip(base * inten, 0, 255).astype(np.uint8)).convert()
    bg.blit(floor, (0, HORIZON_Y))

    vx = WIDTH // 2
    for xb in range(-2400, 3601, 150):
        c = side_color(xb / WIDTH)
        pygame.draw.line(bg, scale_color(c, 0.16), (vx, HORIZON_Y), (xb, HEIGHT), 5)
        pygame.draw.line(bg, scale_color(c, 0.45), (vx, HORIZON_Y), (xb, HEIGHT), 2)
        pygame.draw.line(bg, scale_color(c, 0.85), (vx, HORIZON_Y), (xb, HEIGHT), 1)
    n = 16
    for i in range(1, n + 1):
        y = HORIZON_Y + int(fh * ((i / n) ** 2.2))
        br = 0.25 + 0.6 * (i / n)
        for x0 in range(0, WIDTH, 30):
            c = scale_color(side_color((x0 + 15) / WIDTH), br)
            pygame.draw.line(bg, c, (x0, y), (x0 + 30, y), 2 if i > 9 else 1)
    # horizon rail + glow
    for x0 in range(0, WIDTH, 30):
        c = side_color((x0 + 15) / WIDTH)
        pygame.draw.line(bg, c, (x0, HORIZON_Y), (x0 + 30, HORIZON_Y), 3)
    for x0 in range(0, WIDTH, 60):
        draw_glow(bg, (x0 + 30, HORIZON_Y), 70, side_color((x0 + 30) / WIDTH), 0.35)


def build_static_scene():
    bg = create_gradient_background(WIDTH, HEIGHT)
    rng = random.Random(7)
    # distant stars / bokeh city lights
    for _ in range(90):
        x, y = rng.randrange(WIDTH), rng.randrange(0, HORIZON_Y - 40)
        c = side_color(x / WIDTH)
        bg.set_at((x, y), scale_color(c, 0.6))
    for _ in range(34):
        x, y = rng.randrange(WIDTH), rng.randrange(HORIZON_Y - 150, HORIZON_Y - 10)
        draw_glow(bg, (x, y), rng.randint(5, 12), side_color(x / WIDTH), 0.45)
    # lighting behind each fighter
    draw_glow(bg, (PLAYER_X, 380), 380, (40, 120, 255), 0.62)
    draw_glow(bg, (ENEMY_X, 380), 380, (255, 36, 70), 0.62)
    draw_glow(bg, (WIDTH // 2, 300), 280, (110, 50, 200), 0.28)
    # light beams
    for bx, col, ang in ((110, (60, 150, 255), -9), (450, (60, 220, 255), 11),
                         (760, (255, 70, 110), -11), (1095, (255, 60, 90), 8)):
        beam = pygame.transform.rotate(make_beam(430, 190, 26, col, 58), ang)
        bg.blit(beam, beam.get_rect(midbottom=(bx, HORIZON_Y + 6)))
    paint_floor(bg)
    # neon platforms under the fighters
    for cx, col in ((PLAYER_X, (70, 170, 255)), (ENEMY_X, (255, 70, 90))):
        draw_glow(bg, (cx, GROUND_Y + 4), 150, col, 0.35)
        pygame.draw.ellipse(bg, scale_color(col, 0.55), (cx - 150, GROUND_Y - 14, 300, 44), 2)
        pygame.draw.ellipse(bg, col, (cx - 120, GROUND_Y - 8, 240, 32), 1)
    # vignette
    vx = np.linspace(-1, 1, WIDTH)[:, None]
    vy = np.linspace(-1, 1, HEIGHT)[None, :]
    v = 1 - 0.55 * np.clip((np.sqrt(vx ** 2 * 0.85 + vy ** 2) - 0.55) / 0.9, 0, 1) ** 1.5
    px = pygame.surfarray.pixels3d(bg)
    px[:] = (px * v[..., None]).astype(np.uint8)
    del px
    return bg


def build_haze():
    h = 150
    surf = pygame.Surface((WIDTH, h), pygame.SRCALPHA)
    al = np.zeros((WIDTH, h), dtype=np.float32)
    ys = np.linspace(0, 1, h)
    profile = np.exp(-((ys - 0.62) / 0.28) ** 2)
    xs = np.arange(WIDTH)
    ripple = 0.65 + 0.35 * np.sin(xs * math.tau * 3 / WIDTH) * np.cos(xs * math.tau * 2 / WIDTH + 1.0)
    al[:] = ripple[:, None] * profile[None, :] * 46
    rgb = pygame.surfarray.pixels3d(surf)
    c = np.array([side_color(x / WIDTH) for x in range(WIDTH)], dtype=np.uint8)
    rgb[:] = c[:, None, :]
    del rgb
    a = pygame.surfarray.pixels_alpha(surf)
    a[:] = al.astype(np.uint8)
    del a
    return surf


def build_vignette(color, max_alpha):
    surf = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    rgb = pygame.surfarray.pixels3d(surf)
    rgb[:] = np.array(color, dtype=np.uint8)
    del rgb
    vx = np.linspace(-1, 1, WIDTH)[:, None]
    vy = np.linspace(-1, 1, HEIGHT)[None, :]
    v = np.clip((np.sqrt(vx ** 2 * 0.9 + vy ** 2) - 0.6) / 0.6, 0, 1) ** 1.5
    a = pygame.surfarray.pixels_alpha(surf)
    a[:] = (v * max_alpha).astype(np.uint8)
    del a
    return surf


def build_strip(width, height, top_alpha, bottom_alpha, color=(5, 9, 24)):
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    rgb = pygame.surfarray.pixels3d(surf)
    rgb[:] = np.array(color, dtype=np.uint8)
    del rgb
    a = pygame.surfarray.pixels_alpha(surf)
    a[:] = np.linspace(top_alpha, bottom_alpha, height).astype(np.uint8)[None, :]
    del a
    return surf


# ============================================================
# Fighter sprite assets
# ============================================================
def tinted(sprite, rgb):
    t = sprite.copy()
    t.fill((*rgb, 0), special_flags=pygame.BLEND_RGB_ADD)
    return t


def make_halo_levels(sprite, color, pad=HALO_PAD, levels=6):
    """Soft rim-light derived from the sprite's own silhouette (additive surfaces)."""
    w, h = sprite.get_size()
    mask = pygame.mask.from_surface(sprite, 40)
    sil = pygame.Surface((w + pad * 2, h + pad * 2))
    sil.fill((0, 0, 0))
    sil.blit(mask.to_surface(setcolor=(255, 255, 255, 255), unsetcolor=(0, 0, 0, 255)), (pad, pad))
    sw, sh = sil.get_size()
    tight = pygame.transform.smoothscale(
        pygame.transform.smoothscale(sil, (max(1, sw // 5), max(1, sh // 5))), (sw, sh))
    wide = pygame.transform.smoothscale(
        pygame.transform.smoothscale(sil, (max(1, sw // 12), max(1, sh // 12))), (sw, sh))
    a = pygame.surfarray.array3d(tight)[:, :, 0].astype(np.float32) / 255.0
    b = pygame.surfarray.array3d(wide)[:, :, 0].astype(np.float32) / 255.0
    field = np.clip(a * 1.25 + b * 1.6, 0, 1)
    out = []
    for i in range(levels):
        lv = 0.35 + 0.65 * i / (levels - 1)
        arr = np.clip(field[..., None] * np.array(color, np.float32) * lv, 0, 255).astype(np.uint8)
        out.append(pygame.surfarray.make_surface(arr).convert())
    return out


def make_reflection(sprite, strip=110, strength=0.34):
    w, h = sprite.get_size()
    sh = min(strip, h)
    part = sprite.subsurface((0, h - sh, w, sh)).copy()
    refl = pygame.transform.flip(part, False, True)
    al = pygame.surfarray.pixels_alpha(refl)
    grad = np.linspace(strength, 0.0, sh, dtype=np.float32)[None, :]
    al[:] = (al.astype(np.float32) * grad).astype(np.uint8)
    del al
    return refl


def make_dome(rx, ry, color):
    W, H = rx * 2 + 4, ry * 2 + 4
    xs = (np.arange(W, dtype=np.float32) - W / 2) / rx
    ys = (np.arange(H, dtype=np.float32) - H / 2) / ry
    d = np.sqrt(xs[:, None] ** 2 + ys[None, :] ** 2)
    inside = d <= 1.0
    alpha = inside * (22 + 70 * d ** 4) + np.exp(-((d - 0.985) / 0.012) ** 2) * 200
    surf = pygame.Surface((W, H), pygame.SRCALPHA, 32)
    rgb = pygame.surfarray.pixels3d(surf)
    rim = np.clip((d - 0.7) / 0.3, 0, 1)[..., None]
    base = np.array(color, np.float32)[None, None, :]
    rgb[:] = np.clip(base * (1 - rim) + 255 * rim * 0.55 + base * rim * 0.45, 0, 255).astype(np.uint8)
    del rgb
    al = pygame.surfarray.pixels_alpha(surf)
    al[:] = np.clip(alpha, 0, 255).astype(np.uint8)
    del al
    return surf


# ============================================================
# Effects
# ============================================================
class Particle:
    def __init__(self, x, y, color, velocity=None, life=None, radius=None, gravity=90.0, drag=0.985):
        self.x, self.y = float(x), float(y)
        angle = random.uniform(0, math.tau)
        speed = random.uniform(80, 360)
        self.vx, self.vy = velocity or (math.cos(angle) * speed, math.sin(angle) * speed)
        self.life = self.max_life = life or random.uniform(0.25, 0.75)
        self.color = tuple(color[:3])
        self.radius = radius or random.randint(2, 5)
        self.gravity = gravity
        self.drag = drag

    def update(self, dt):
        self.life -= dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vx *= self.drag
        self.vy *= self.drag
        self.vy += self.gravity * dt

    def draw(self, surface):
        if self.life <= 0:
            return
        k = self.life / self.max_life
        r = max(2, int(self.radius * (0.5 + 0.5 * k) * 3))
        draw_glow(surface, (self.x, self.y), r, self.color, k)
        ix, iy = int(self.x), int(self.y)
        if k > 0.45 and 0 <= ix < WIDTH and 0 <= iy < HEIGHT:
            surface.set_at((ix, iy), (255, 255, 255))


class FloatingText:
    def __init__(self, x, y, text, color, size=28):
        self.x, self.y = x, y
        self.text, self.color, self.size = text, color, size
        self.life = 1.0

    def update(self, dt):
        self.life -= dt
        self.y -= 48 * dt

    def draw(self, surface, font=None):
        if self.life > 0:
            img = glow_text(get_font(int(self.size * 1.25)), self.text, self.color, spread=5)
            img.set_alpha(int(255 * min(1, self.life * 1.4)))
            surface.blit(img, (int(self.x - img.get_width() / 2), int(self.y - img.get_height() / 2)))


class Shockwave:
    def __init__(self, x, y, color, max_r=220, life=0.55, delay=0.0):
        self.x, self.y, self.color = x, y, color
        self.max_r, self.life, self.max_life, self.delay = max_r, life, life, delay

    def update(self, dt):
        if self.delay > 0:
            self.delay -= dt
        else:
            self.life -= dt

    def draw(self, surface):
        if self.delay > 0 or self.life <= 0:
            return
        p = 1 - self.life / self.max_life
        r = int(self.max_r * ease_out_cubic(p))
        fade = 1 - p
        if r < 3 or fade < 0.1:
            return
        # keep the colour bright and thin the ring out as it fades (no dark outline over the art)
        pygame.draw.circle(surface, self.color, (int(self.x), int(self.y)), r, max(1, int(7 * fade)))
        if fade > 0.35:
            pygame.draw.circle(surface, (255, 255, 255), (int(self.x), int(self.y)), max(2, r - 3), 1)


class ImpactFlash:
    def __init__(self, x, y, radius, color, life=0.3):
        self.x, self.y, self.radius, self.color = x, y, radius, color
        self.life = self.max_life = life

    def update(self, dt):
        self.life -= dt

    def draw(self, surface):
        if self.life <= 0:
            return
        k = self.life / self.max_life
        r = int(self.radius * (1.25 - 0.35 * k))
        draw_glow(surface, (self.x, self.y), r, self.color, k)
        draw_glow(surface, (self.x, self.y), int(r * 0.45), (255, 255, 255), k * 0.9)


class Projectile:
    def __init__(self, x, y, tx, ty, color, speed, kind, direction=1, damage=0, size=14, outer=None):
        self.x0, self.y0 = float(x), float(y)
        self.x, self.y = float(x), float(y)
        self.tx, self.ty = float(tx), float(ty)
        self.color = tuple(color[:3])
        self.outer = tuple((outer or color)[:3])
        self.speed, self.kind, self.direction = speed, kind, direction
        self.damage, self.size = damage, size
        self.trail = deque(maxlen=12)

    def update(self, dt):
        self.trail.append((self.x, self.y))
        self.x += self.direction * self.speed * dt
        span = (self.tx - self.x0) or 1.0
        prog = clamp((self.x - self.x0) / span)
        self.y = lerp(self.y0, self.ty, prog)
        if (self.x - self.tx) * self.direction >= 0:
            self.x, self.y = self.tx, self.ty
            return True
        return False

    def draw(self, surface):
        n = len(self.trail)
        for i, (px, py) in enumerate(self.trail):
            k = (i + 1) / n
            draw_glow(surface, (px, py), int(self.size * 1.4 * k) + 4, self.outer, 0.5 * k)
        draw_glow(surface, (self.x, self.y), int(self.size * 3.0), self.outer, 0.85)
        draw_glow(surface, (self.x, self.y), int(self.size * 1.7), self.color, 1.0)
        tail = int(self.size * 4)
        pygame.draw.line(surface, self.color, (int(self.x - self.direction * tail), int(self.y)),
                         (int(self.x), int(self.y)), max(2, int(self.size * 0.35)))
        pygame.draw.circle(surface, (255, 255, 255), (int(self.x), int(self.y)), max(3, int(self.size * 0.45)))


# ============================================================
# Fighter (uses the real PNG artwork + layered effects)
# ============================================================
class CyberFighter:
    """Sprite-based fighter: breathing, lunge, recoil, glow, shield dome, heal aura."""

    def __init__(self, x, ground_y, team, sprite=None):
        self.x = float(x)
        self.ground_y = ground_y
        self.team = team
        self.is_hero = team == "player"
        self.facing = 1 if self.is_hero else -1
        self.primary = COLORS["cyan"] if self.is_hero else COLORS["red"]
        self.secondary = COLORS["blue"] if self.is_hero else COLORS["pink"]
        self.glow_color = (60, 150, 255) if self.is_hero else (255, 40, 60)
        self.sprite = sprite
        self.phase = random.random() * 5
        self.reset()
        if sprite is not None:
            self.build_assets()

    def reset(self):
        self.attack_anim = 0.0
        self.hit_flash = 0.0
        self.hit_duration = 0.28
        self.hit_power = 24.0
        self.shield_time = 0.0
        self.shield_intro = 0.0
        self.shield_hit = 0.0
        self.shield_hit_pos = (0, 0)
        self.special_anim = 0.0
        self.heal_anim = 0.0
        self.defeated = False
        self.defeat_t = 0.0
        self.offset_x = 0.0
        self.offset_y = 0.0

    # ---------- assets ----------
    def build_assets(self):
        s = self.sprite
        self.w, self.h = s.get_size()
        self.frames = []
        for i in range(BREATH_FRAMES):
            k = 1.0 + 0.012 * (2 * i / (BREATH_FRAMES - 1) - 1)
            if abs(k - 1.0) < 1e-3:
                self.frames.append(s)
            else:
                self.frames.append(pygame.transform.smoothscale(s, (int(round(self.w * k)), int(round(self.h * k)))))
        self.tint_red = tinted(s, (235, 25, 25))
        self.tint_white = tinted(s, (170, 170, 170))
        self.tint_blue = tinted(s, (30, 110, 255))
        self.halos = make_halo_levels(s, self.glow_color)
        self.reflection = make_reflection(s)
        self.ground_glows = []
        for i in range(1, 7):
            g = get_glow(110, self.glow_color, i / 6.0)
            self.ground_glows.append(pygame.transform.smoothscale(g, (int(self.w * 1.25), 58)))
        self.dome_rx = int(self.w * 0.62) + 24
        self.dome_ry = int(self.h * 0.56) + 20
        self.shield_color = (40, 190, 255) if self.is_hero else (255, 45, 75)
        self.dome = make_dome(self.dome_rx, self.dome_ry, self.shield_color)

    # ---------- anchors ----------
    def body_pos(self):
        return (self.x + self.offset_x, self.ground_y - self.h * 0.55)

    def hand_pos(self):
        return (self.x + self.offset_x + self.facing * self.w * 0.36, self.ground_y - self.h * 0.58)

    def dome_edge_x(self):
        return self.x + self.offset_x + self.facing * self.dome_rx * 0.96

    # ---------- state ----------
    def trigger(self, action):
        if action == "attack":
            self.attack_anim = ATTACK_ANIM
        elif action == "shield":
            self.shield_time = SHIELD_DURATION
            self.shield_intro = 0.28
        elif action == "special":
            self.special_anim = SPECIAL_ANIM
        elif action == "heal":
            self.heal_anim = HEAL_ANIM

    def hit(self, duration=0.28, power=24.0):
        self.hit_flash = duration
        self.hit_duration = duration
        self.hit_power = power

    def special_charge_progress(self):
        el = SPECIAL_ANIM - self.special_anim
        return clamp(el / SPECIAL_CHARGE) if self.special_anim > 0 and el < SPECIAL_CHARGE else 0.0

    def update(self, dt):
        self.phase += dt * 2.2
        self.attack_anim = max(0, self.attack_anim - dt)
        self.hit_flash = max(0, self.hit_flash - dt)
        self.shield_time = max(0, self.shield_time - dt)
        self.shield_intro = max(0, self.shield_intro - dt)
        self.shield_hit = max(0, self.shield_hit - dt)
        self.special_anim = max(0, self.special_anim - dt)
        self.heal_anim = max(0, self.heal_anim - dt)
        if self.defeated:
            self.defeat_t += dt
        off = 0.0
        if self.attack_anim > 0:
            p = 1 - self.attack_anim / ATTACK_ANIM
            off += self.facing * math.sin(p * math.pi) * (80 if self.is_hero else 62)
        if self.special_anim > 0:
            el = SPECIAL_ANIM - self.special_anim
            if el < SPECIAL_CHARGE:
                off -= self.facing * 16 * math.sin(el / SPECIAL_CHARGE * math.pi / 2)
            else:
                p = (el - SPECIAL_CHARGE) / (SPECIAL_ANIM - SPECIAL_CHARGE)
                off += self.facing * (-16 * (1 - p) + 36 * math.sin(p * math.pi))
        if self.hit_flash > 0:
            off -= self.facing * self.hit_power * (self.hit_flash / self.hit_duration) ** 1.4
        self.offset_x = off
        self.offset_y = math.sin(self.phase) * 3 + (min(self.defeat_t, 0.6) * 10 if self.defeated else 0)

    # ---------- drawing ----------
    def draw(self, surface, t, charge=0.0):
        gx = int(self.x + self.offset_x)
        gy = int(self.ground_y + self.offset_y)
        pulse = 0.5 + 0.5 * math.sin(t * 2.4 + self.phase)
        chest = (gx, gy - int(self.h * 0.55))
        cp = self.special_charge_progress()

        # ground glow + reflection
        gg = self.ground_glows[min(5, int(pulse * 5 + charge * 2))]
        surface.blit(gg, gg.get_rect(center=(int(self.x + self.offset_x * 0.6), self.ground_y + 6)),
                     special_flags=pygame.BLEND_RGB_ADD)
        surface.blit(self.reflection, self.reflection.get_rect(midtop=(gx, self.ground_y + 4)))

        # light spill / energy aura
        aura = 0.26 + 0.14 * pulse + 0.35 * charge + 0.3 * cp
        if self.hit_flash > 0:
            aura += 0.25
        draw_glow(surface, chest, int(self.h * 0.52), self.glow_color, aura)

        # silhouette rim light
        hi = min(len(self.halos) - 1, int(clamp(pulse * 0.8 + charge * 0.5 + cp * 0.6) * len(self.halos)))
        halo = self.halos[hi]
        surface.blit(halo, halo.get_rect(midbottom=(gx, gy + HALO_PAD)), special_flags=pygame.BLEND_RGB_ADD)

        # the character
        k = int((0.5 + 0.5 * math.sin(self.phase * 0.9)) * (BREATH_FRAMES - 1))
        frame = self.frames[k]
        frame.set_alpha(255)
        if self.defeated:
            frame.set_alpha(int(max(90, 210 - self.defeat_t * 80)))
            if random.random() < 0.25:
                gx += random.randint(-4, 4)
        surface.blit(frame, frame.get_rect(midbottom=(gx, gy)))
        base_rect = self.sprite.get_rect(midbottom=(gx, gy))
        if self.hit_flash > 0:
            kk = self.hit_flash / self.hit_duration
            self.tint_red.set_alpha(int(210 * clamp(kk * 1.5)))
            surface.blit(self.tint_red, base_rect)
            if kk > 0.7:
                self.tint_white.set_alpha(int(150 * (kk - 0.7) / 0.3))
                surface.blit(self.tint_white, base_rect)
        if cp > 0:
            self.tint_blue.set_alpha(int(90 + 90 * math.sin(t * 30) ** 2) if cp < 1 else 0)
            surface.blit(self.tint_blue, base_rect)

        # effects
        self.draw_shield(surface, t, gx, gy)
        if self.is_hero:
            self.draw_special(surface, t, gx, chest)
        self.draw_heal(surface, t, gx, gy)

    def draw_shield(self, surface, t, gx, gy):
        if self.shield_time <= 0:
            return
        rx, ry = self.dome_rx, self.dome_ry
        cx, cy = gx, gy + 22 - ry
        fade = clamp(self.shield_time / 0.6)
        if self.shield_time < 0.8:
            fade *= 0.6 + 0.4 * math.sin(t * 40)
        dome = self.dome
        if self.shield_intro > 0:
            s = ease_out_back(1 - self.shield_intro / 0.28)
            dome = pygame.transform.scale(self.dome, (max(2, int(self.dome.get_width() * s)),
                                                     max(2, int(self.dome.get_height() * s))))
        boost = 70 * (self.shield_hit / 0.3) if self.shield_hit > 0 else 0
        dome.set_alpha(int(clamp((200 + boost) * fade * (0.88 + 0.12 * math.sin(t * 6)), 0, 255)))
        surface.blit(dome, dome.get_rect(center=(cx, cy)))
        if self.shield_intro > 0:
            return
        col = mix_color(self.shield_color, (255, 255, 255), 0.35 + 0.25 * math.sin(t * 7))
        # rotating arcs
        for j, (scale, speed, wd) in enumerate(((0.97, 1.8, 3), (0.88, -2.4, 2))):
            r = pygame.Rect(0, 0, int(rx * 2 * scale), int(ry * 2 * scale))
            r.center = (cx, cy)
            for i in range(3):
                a0 = t * speed + i * math.tau / 3
                pygame.draw.arc(surface, scale_color(col, fade), r, a0, a0 + 0.8, wd)
        # orbiting energy motes
        for i in range(10):
            a = t * 1.6 + i * math.tau / 10
            px = cx + math.cos(a) * rx * 0.95
            py = cy + math.sin(a) * ry * 0.95
            draw_glow(surface, (px, py), 10, self.shield_color, 0.8 * fade)
        # impact ripple where an enemy bolt was absorbed
        if self.shield_hit > 0:
            k = self.shield_hit / 0.3
            draw_glow(surface, self.shield_hit_pos, int(40 + 70 * (1 - k)), (150, 240, 255), k)
        # remaining duration bar
        bw = 130
        bx, by = gx - bw // 2, self.ground_y + 12
        pygame.draw.rect(surface, (10, 30, 50), (bx, by, bw, 6), border_radius=3)
        pygame.draw.rect(surface, self.shield_color, (bx, by, int(bw * self.shield_time / SHIELD_DURATION), 6), border_radius=3)
        blit_text(surface, get_font(18), f"SHIELD {self.shield_time:.1f}s", self.shield_color, (gx, by - 4), "midbottom")

    def draw_special(self, surface, t, gx, chest):
        if self.special_anim <= 0:
            return
        el = SPECIAL_ANIM - self.special_anim
        hx, hy = gx + self.facing * int(self.w * 0.30), chest[1] - 6
        if el < SPECIAL_CHARGE:
            cp = el / SPECIAL_CHARGE
            draw_glow(surface, chest, int(110 + 120 * cp), (60, 130, 255), 0.5 + 0.4 * math.sin(t * 22) ** 2)
            draw_glow(surface, (hx, hy), int(30 + 110 * cp), (150, 120, 255), 0.9)
            draw_glow(surface, (hx, hy), int(14 + 50 * cp), (255, 255, 255), 0.9)
            ring_r = int(210 * (1 - cp) + 36)
            pygame.draw.circle(surface, scale_color((120, 200, 255), 0.5 + cp * 0.5), (hx, hy), ring_r, 3)
            pygame.draw.circle(surface, scale_color((200, 160, 255), 0.4 + cp * 0.5), (hx, hy), int(ring_r * 0.7), 1)
            for _ in range(6):
                a = random.uniform(0, math.tau)
                l0, l1 = random.uniform(20, 50), random.uniform(60, 60 + 110 * cp)
                pygame.draw.line(surface, random.choice(((150, 220, 255), (255, 255, 255))),
                                 (hx + math.cos(a) * l0, hy + math.sin(a) * l0),
                                 (hx + math.cos(a) * l1, hy + math.sin(a) * l1), 2)
        else:
            p = (el - SPECIAL_CHARGE) / (SPECIAL_ANIM - SPECIAL_CHARGE)
            draw_glow(surface, (hx, hy), int(150 * (1 - p) + 20), (140, 170, 255), 1 - p)
            draw_glow(surface, (hx, hy), int(60 * (1 - p) + 10), (255, 255, 255), 1 - p)

    def draw_heal(self, surface, t, gx, gy):
        if self.heal_anim <= 0:
            return
        p = self.heal_anim / HEAL_ANIM
        base = (70, 255, 200)
        draw_glow(surface, (gx, gy - int(self.h * 0.5)), int(self.h * 0.5), base, 0.45 * clamp(p * 1.5))
        for i in range(3):
            q = ((1 - p) * 1.5 + i / 3.0) % 1.0
            y = gy - q * self.h
            rx = self.w * 0.38 * (0.92 + 0.08 * math.sin(t * 8 + i))
            col = scale_color(base, (1 - q) * clamp(p * 2 + 0.3))
            pygame.draw.ellipse(surface, col, (gx - rx, y - 13, rx * 2, 26), 2)
            pygame.draw.ellipse(surface, scale_color((200, 255, 240), (1 - q) * 0.5 * clamp(p * 2)),
                                (gx - rx + 6, y - 9, rx * 2 - 12, 18), 1)


# ============================================================
# Hacked-screen cinematic (shown after Round 3, before the boss fight)
# ============================================================
HK_GREEN = (40, 255, 140)
HK_CYAN = (70, 225, 255)
HK_RED = (255, 40, 70)
HK_WHITE = (240, 250, 255)

HK_LOG_LINES = [
    ("> INTRUSION DETECTED ........ SECTOR 7", HK_GREEN),
    ("> FIREWALL .................. BREACHED", HK_GREEN),
    ("> G.ONE PROTOCOL ............ COMPROMISED", HK_RED),
    ("> RA.ONE.EXE ................ INJECTED", HK_RED),
    ("> CORE OVERRIDE ............. IN PROGRESS", HK_RED),
]
HK_CHARS = "01ABCDEFXYZ#$%&<>/\\|{}[]=+*"


def _hk_clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _hk_ease_out(t):
    return 1 - (1 - _hk_clamp(t)) ** 3


def _hk_bloom(screen, strength=0.7, down=5):
    """Cheap bloom: blur a downscaled copy and add it back."""
    w, h = screen.get_size()
    small = pygame.transform.smoothscale(screen, (max(1, w // down), max(1, h // down)))
    small = pygame.transform.smoothscale(small, (max(1, w // (down * 2)), max(1, h // (down * 2))))
    big = pygame.transform.smoothscale(small, (w, h))
    big.fill((int(255 * strength),) * 3, special_flags=pygame.BLEND_RGB_MULT)
    screen.blit(big, (0, 0), special_flags=pygame.BLEND_RGB_ADD)


class _HackAssets:
    def __init__(self, w, h):
        self.w, self.h = w, h
        mono = "consolas,couriernew,dejavusansmono,monospace"
        self.rain_font = pygame.font.SysFont(mono, 18, bold=True)
        self.log_font = pygame.font.SysFont(mono, max(15, h // 40), bold=True)
        self.small = pygame.font.SysFont(mono, max(12, h // 55), bold=True)
        self.title_font = pygame.font.SysFont("impact,arialblack,arial", int(h * 0.15), bold=True)
        self.sub_font = pygame.font.SysFont("arial", int(h * 0.036), bold=True)
        self.cw, self.ch = 16, 20

        # glyph cache: [palette][shade][char]
        self.palettes = {
            "g": [(235, 255, 245), (90, 255, 170), (40, 220, 120), (25, 160, 90), (15, 105, 62), (8, 60, 36)],
            "r": [(255, 235, 238), (255, 110, 130), (235, 45, 70), (170, 30, 50), (110, 20, 34), (60, 12, 20)],
        }
        self.glyphs = {
            key: [{c: self.rain_font.render(c, True, col) for c in HK_CHARS} for col in cols]
            for key, cols in self.palettes.items()
        }

        # scanlines + vignette
        # scanlines + vignette combined into one multiply-surface (fast, opaque)
        self.scan = pygame.Surface((w, h))
        self.scan.fill((255, 255, 255))
        steps = 60
        for i in range(steps):
            v = int(255 - 210 * (1 - i / steps) ** 2.4)
            pygame.draw.rect(self.scan, (v, v, v), (i * 2, i * 2, w - i * 4, h - i * 4), 3)
        for y in range(0, h, 3):
            self.scan.fill((200, 200, 200), (0, y, w, 1), special_flags=pygame.BLEND_RGB_MULT)
        self.vig = None

        # red core glow behind the title
        gw, gh = int(w * 0.9), int(h * 0.5)
        self.core = pygame.Surface((gw, gh))
        for k in range(24):
            f = 1 - k / 24
            a = (k / 24) ** 1.6
            pygame.draw.ellipse(self.core, (int(255 * a * 0.55), int(25 * a * 0.55), int(55 * a * 0.55)),
                                (gw / 2 - gw / 2 * f, gh / 2 - gh / 2 * f, gw * f, gh * f))
        self.core = pygame.transform.smoothscale(
            pygame.transform.smoothscale(self.core, (gw // 6, gh // 6)), (gw, gh))
        self.rain_layer = pygame.Surface((w, h))
        self.rain_layer.set_colorkey((0, 0, 0))

    def add_text(self, font, s, color, k=1.0):
        """Opaque black-backed text that is safe to blit with BLEND_RGB_ADD."""
        img = font.render(s, True, color, (0, 0, 0)).convert()
        if k < 1.0:
            v = int(255 * _hk_clamp(k))
            img.fill((v, v, v), special_flags=pygame.BLEND_RGB_MULT)
        return img

    def text(self, font, s, color):
        return font.render(s, True, color)


def _hk_rain_columns(w, rng):
    cols = []
    for i in range(w // 16 + 1):
        cols.append({
            "x": i * 16,
            "speed": rng.uniform(160, 520),
            "off": rng.uniform(0, 2000),
            "len": rng.randint(8, 22),
            "red": rng.random() < 0.28,
        })
    return cols


def _hk_draw_rain(screen, assets, cols, t, red_bias, alpha):
    h = assets.h
    layer = assets.rain_layer
    layer.fill((0, 0, 0))
    ch_h = assets.ch
    for ci, c in enumerate(cols):
        head = (t * c["speed"] + c["off"]) % (h + c["len"] * ch_h) - ch_h
        pal = "r" if (c["red"] or (ci % 7 < red_bias * 7)) else "g"
        glyphs = assets.glyphs[pal]
        for k in range(c["len"]):
            y = int(head - k * ch_h)
            if y < -ch_h or y > h:
                continue
            shade = 0 if k == 0 else min(5, 1 + k * 5 // c["len"])
            ch = HK_CHARS[(ci * 7 + k * 13 + int(t * 7 + k * 0.5)) % len(HK_CHARS)]
            layer.blit(glyphs[shade][ch], (c["x"], y))
    layer.set_alpha(int(255 * alpha))
    screen.blit(layer, (0, 0))


def _hk_glitch_slices(screen, rng, intensity):
    """Horizontal tearing + RGB split using slices of the current frame."""
    w, h = screen.get_size()
    frame = screen.copy()
    for _ in range(int(2 + 7 * intensity)):
        sh = rng.randint(6, int(h * 0.09))
        y = rng.randint(0, h - sh)
        dx = rng.randint(-int(w * 0.07), int(w * 0.07)) * (0.4 + intensity)
        screen.blit(frame, (int(dx), y), (0, y, w, sh))
        if rng.random() < 0.5:
            tint = pygame.Surface((w, sh))
            base = rng.choice(((255, 0, 60), (0, 255, 255), (255, 255, 255)))
            f = (30 + 60 * intensity) / 255
            tint.fill((int(base[0] * f), int(base[1] * f), int(base[2] * f)))
            screen.blit(tint, (0, y), special_flags=pygame.BLEND_RGB_ADD)


def _hk_noise(screen, rng, amount):
    w, h = screen.get_size()
    for _ in range(int(amount)):
        x, y = rng.randint(0, w), rng.randint(0, h)
        v = rng.randint(90, 255)
        screen.fill((v, v, v), (x, y, rng.randint(2, 40), rng.randint(1, 3)))


def _hk_rings(screen, cx, cy, t, strength):
    """Rotating broken HUD rings that bloom into a glowing target."""
    layer = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    for i, (r, col, spd, wd) in enumerate(((210, HK_RED, 0.9, 3), (270, HK_CYAN, -0.6, 2), (330, HK_RED, 0.4, 2))):
        rect = pygame.Rect(cx - r, cy - r, r * 2, r * 2)
        for seg in range(6):
            a0 = t * spd + seg * math.tau / 6
            pygame.draw.arc(layer, (*col, int(210 * strength)), rect, a0, a0 + 0.7, wd)
    screen.blit(layer, (0, 0))


def show_hacked_screen(screen, clock, duration=5.0):
    w, h = screen.get_size()
    rng = random.Random()
    assets = _HackAssets(w, h)
    cols = _hk_rain_columns(w, rng)
    prev = screen.copy()
    cx, cy = w // 2, int(h * 0.46)

    try:
        pygame.mixer.music.stop()
    except pygame.error:
        pass

    t = 0.0
    while t < duration:
        dt = min(clock.tick(60) / 1000.0, 0.04)
        t += dt
        p = t / duration

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.event.post(pygame.event.Event(pygame.QUIT))
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False

        # ---- base: last game frame tearing apart, then black void
        screen.fill((2, 3, 8))
        if t < 0.75:
            screen.blit(prev, (0, 0))
            screen.fill((int(255 * (t / 0.75) ** 2),) * 3, special_flags=pygame.BLEND_RGB_SUB)
        rain_alpha = _hk_clamp((t - 0.35) / 0.8)
        if rain_alpha > 0:
            _hk_draw_rain(screen, assets, cols, t, red_bias=_hk_clamp((t - 1.2) / 2.0), alpha=rain_alpha * 0.85)

        # ---- red core glow + rings
        pulse = 0.75 + 0.25 * math.sin(t * 8)
        if t > 0.9:
            k = _hk_ease_out((t - 0.9) / 0.6)
            g = assets.core.copy()
            v = int(255 * k * pulse)
            g.fill((v, v, v), special_flags=pygame.BLEND_RGB_MULT)
            screen.blit(g, (cx - g.get_width() // 2, cy - g.get_height() // 2),
                        special_flags=pygame.BLEND_RGB_ADD)
            _hk_rings(screen, cx, cy, t, k)

        # ---- terminal log (left panel)
        px, py = int(w * 0.04), int(h * 0.62)
        pw, ph = int(w * 0.42), int(h * 0.27)
        if t > 0.7:
            panel = pygame.Surface((pw, ph), pygame.SRCALPHA)
            panel.fill((2, 12, 10, 190))
            pygame.draw.rect(panel, (*HK_GREEN, 220), panel.get_rect(), 2, border_radius=4)
            screen.blit(panel, (px, py))
            line_h = int(h * 0.04)
            shown = int((t - 0.8) / 0.38)
            for i, (txt, col) in enumerate(HK_LOG_LINES[: max(0, shown)]):
                part = txt if i < shown - 1 else txt[: int(len(txt) * _hk_clamp(((t - 0.8) % 0.38) / 0.3))]
                screen.blit(assets.text(assets.log_font, part, col), (px + 14, py + 12 + i * line_h))
            if int(t * 4) % 2 == 0:
                screen.fill(HK_GREEN, (px + pw - 26, py + ph - 24, 12, 4))

        # ---- override progress bar (right)
        bw, bh = int(w * 0.30), 16
        bx, by = int(w * 0.66), int(h * 0.80)
        prog = _hk_clamp((t - 0.9) / (duration - 1.5))
        pygame.draw.rect(screen, (30, 6, 12), (bx, by, bw, bh), border_radius=6)
        pygame.draw.rect(screen, HK_RED, (bx, by, int(bw * prog), bh), border_radius=6)
        pygame.draw.rect(screen, (255, 120, 140), (bx, by, bw, bh), 2, border_radius=6)
        screen.blit(assets.text(assets.small, f"SYSTEM OVERRIDE  {int(prog * 100):03d}%", (255, 150, 165)),
                    (bx, by - int(h * 0.04)))

        # ---- warning banner (top, blinking)
        if t > 0.5:
            blink = int(t * 6) % 2 == 0
            tri = [(w // 2 - 190, int(h * 0.075)), (w // 2 - 170, int(h * 0.075) - 30), (w // 2 - 150, int(h * 0.075))]
            col = HK_RED if blink else (255, 150, 160)
            pygame.draw.polygon(screen, col, tri, 3)
            screen.blit(assets.text(assets.small, "!", col), (w // 2 - 174, int(h * 0.075) - 22))
            wt = assets.text(assets.log_font, "CRITICAL ALERT  //  UNAUTHORIZED ACCESS", col)
            screen.blit(wt, (w // 2 - 130, int(h * 0.075) - 20))

        # ---- title slam with chromatic aberration
        if t > 1.2:
            k = _hk_ease_out((t - 1.2) / 0.35)
            scale = 1.6 - 0.6 * k
            settle = _hk_clamp((t - 1.2) / 0.9)
            split = int((1 - settle) * 26 + 3 + (6 if rng.random() < 0.08 else 0))
            flicker = 0.55 if rng.random() < 0.04 else 1.0
            for off, col in ((-split, (0, 255, 255)), (split, (255, 0, 70)), (0, HK_WHITE)):
                img = assets.add_text(assets.title_font, "SYSTEM HACKED", col, k * flicker)
                img = pygame.transform.smoothscale(
                    img, (int(img.get_width() * scale), int(img.get_height() * scale)))
                screen.blit(img, img.get_rect(center=(cx + off, cy)), special_flags=pygame.BLEND_RGB_ADD)
        if t > 2.0:
            k = _hk_clamp((t - 2.0) / 0.5)
            sub = assets.text(assets.sub_font, "RA.ONE HAS TAKEN CONTROL", (255, 170, 180))
            sub.set_alpha(int(255 * k))
            screen.blit(sub, sub.get_rect(center=(cx, cy + int(h * 0.12))))
            sub2 = assets.text(assets.small, "G.ONE — LAST DEFENSE ONLINE", HK_CYAN)
            sub2.set_alpha(int(255 * k * (0.6 + 0.4 * math.sin(t * 6))))
            screen.blit(sub2, sub2.get_rect(center=(cx, cy + int(h * 0.17))))

        # ---- glitching (heavier at start, on the slam, and near the end)
        intensity = 0.0
        if t < 0.9:
            intensity = 1.0
        elif 1.2 < t < 1.55:
            intensity = 0.9
        elif rng.random() < 0.12:
            intensity = 0.45
        if p > 0.88:
            intensity = max(intensity, (p - 0.88) / 0.12)
        if intensity > 0:
            _hk_glitch_slices(screen, rng, intensity)
            _hk_noise(screen, rng, 40 + 260 * intensity)

        # ---- bloom, scanlines, vignette, slam shake
        _hk_bloom(screen, 0.75, 8)
        screen.blit(assets.scan, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        if 1.2 < t < 1.5:
            frame = screen.copy()
            screen.fill((0, 0, 0))
            screen.blit(frame, (rng.randint(-10, 10), rng.randint(-8, 8)))

        # ---- final white-red flash into black
        if p > 0.9:
            k = (p - 0.9) / 0.1
            fade = pygame.Surface((w, h))
            fade.fill((0, 0, 0))
            fade.set_alpha(int(255 * k))
            screen.blit(fade, (0, 0))

        pygame.display.flip()

    return True



def play_looping_ending_video(screen, clock, video_path, clip_seconds=6.0):
    """Loop the first `clip_seconds` of a video, including its audio, until ESC."""
    import shutil
    import subprocess
    import tempfile

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"ERROR: Could not open ending video: {video_path}")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 1 or fps > 240:
        fps = FPS
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames_to_play = min(total_frames, max(1, int(round(fps * clip_seconds))))
    if frames_to_play <= 0:
        cap.release()
        print("ERROR: Ending video has no playable frames.")
        return False

    # Extract the same opening segment as a temporary WAV. Pygame's mixer can
    # play WAV/MP3 audio but cannot directly decode the audio stream in an MP4.
    temp_audio = None
    audio_started = False
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            ffmpeg = None

    if ffmpeg and pygame.mixer.get_init():
        try:
            temp_file = tempfile.NamedTemporaryFile(prefix="ai_superpowers_ending_", suffix=".wav", delete=False)
            temp_audio = temp_file.name
            temp_file.close()
            command = [
                ffmpeg, "-y", "-loglevel", "error", "-i", video_path,
                "-t", str(float(clip_seconds)), "-vn", "-ac", "2",
                "-ar", "44100", "-c:a", "pcm_s16le", temp_audio,
            ]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            pygame.mixer.music.stop()
            pygame.mixer.music.load(temp_audio)
            pygame.mixer.music.set_volume(1.0)
            pygame.mixer.music.play(-1)
            audio_started = True
            print(f"Ending video audio enabled: first {clip_seconds:.1f} seconds, looping.")
        except (OSError, subprocess.CalledProcessError, pygame.error) as exc:
            print(f"WARNING: Could not start ending-video audio: {exc}")
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass
    else:
        print("WARNING: Audio needs pygame mixer and FFmpeg. Install FFmpeg to hear the ending clip audio.")

    pygame.display.set_caption("AI SUPERPOWERS // FINAL TRANSMISSION")
    running = True
    frame_index = 0
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
            if not running:
                break

            if frame_index >= frames_to_play:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                frame_index = 0

            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                frame_index = 0
                ok, frame = cap.read()
            if not ok:
                print("ERROR: Could not decode ending video frame.")
                break

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = cv2.resize(frame, screen.get_size(), interpolation=cv2.INTER_AREA)
            frame_surface = pygame.surfarray.make_surface(np.transpose(frame, (1, 0, 2)))
            screen.blit(frame_surface, (0, 0))
            pygame.display.flip()
            frame_index += 1
            clock.tick(max(1, int(round(fps))))
    finally:
        cap.release()
        if audio_started:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass
        if temp_audio and os.path.isfile(temp_audio):
            try:
                os.remove(temp_audio)
            except OSError:
                pass
    return False


# ============================================================
# Game
# ============================================================
class CyberpunkGame:
    def __init__(self):
        pygame.init()
        self.music_loaded = False
        self.main_music_volume = 0.35
        project_dir = os.path.dirname(os.path.abspath(__file__))
        self.main_music_path = os.path.join(project_dir, "Raftaarein Ra One 128 Kbps.mp3")
        self.bonus_music_path = os.path.join(project_dir, "Ra-One-Humming-Ringtone.mp3")
        self.bonus_music_loaded = False
        # User-provided victory sounds; no AI-generated voice clips are loaded.
        self.result_audio_channel = None
        self.result_audio_sounds = {}
        self.bonus_music_volume = 0.16
        self._voice_duck_until = 0.0
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.set_num_channels(max(8, pygame.mixer.get_num_channels()))
            self.result_audio_channel = pygame.mixer.Channel(6)
            result_audio_dir = os.path.join(project_dir, "assets", "result_audio")
            for result_name in ("gone_wins", "raone_wins"):
                result_path = os.path.join(result_audio_dir, f"{result_name}.wav")
                if os.path.isfile(result_path):
                    try:
                        self.result_audio_sounds[result_name] = pygame.mixer.Sound(result_path)
                        print(f"Victory audio loaded: {result_path}")
                    except pygame.error as exc:
                        print(f"WARNING: Could not load victory audio {result_path}: {exc}")
                else:
                    print(f"INFO: Optional victory audio not found yet: {result_path}")
            if os.path.isfile(self.main_music_path):
                pygame.mixer.music.load(self.main_music_path)
                pygame.mixer.music.set_volume(self.main_music_volume)
                self.music_loaded = True
                print(f"Background music loaded: {self.main_music_path}")
            else:
                print(f"WARNING: Background music file not found: {self.main_music_path}")
            if os.path.isfile(self.bonus_music_path):
                self.bonus_music_loaded = True
                print(f"Bonus-round ringtone found: {self.bonus_music_path}")
            else:
                print(f"WARNING: Bonus ringtone not found: {self.bonus_music_path}")
            # AI voice clips intentionally disabled. Only user-provided final-result audio is used.
        except pygame.error as exc:
            print(f"WARNING: Audio could not be initialized: {exc}")
        pygame.display.set_caption("AI SUPERPOWERS // G.ONE vs RA.ONE")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 25)
        self.small = pygame.font.Font(None, 20)
        self.medium = pygame.font.Font(None, 32)
        self.large = pygame.font.Font(None, 54)
        self.title_font = pygame.font.Font(None, 34)

        # Character artwork (fails with a clear message if missing)
        self.gone_sprite = load_character("gone.png", size=(350, 440))  # Same target height as RA.ONE
        self.raone_sprite = load_character("raone.png", size=(350, 440))
        self.raone_action_label = "WAIT"
        self.raone_action_color = COLORS["muted"]
        self.raone_action_until = 0.0

        # Pre-rendered scenery and HUD layers
        self.gradient_background = create_gradient_background(WIDTH, HEIGHT)
        self.bg_static = build_static_scene()
        self.far_layer = build_skyline(11, 300, 90, 270, (40, 90), (8, 14, 40, 235), 150, 0.30, 0.45)
        self.near_layer = build_skyline(23, 260, 60, 210, (50, 120), (4, 7, 22, 255), 220, 0.22, 0.85)
        self.haze = build_haze()
        self.vig_blue = build_vignette((10, 60, 200), 170)
        self.vig_red = build_vignette((200, 10, 30), 190)
        self.hud_top = build_strip(WIDTH, HUD_TOP, 235, 120)
        self.hud_dock = build_strip(WIDTH, HEIGHT - DOCK_Y, 150, 245)
        for x0 in range(0, WIDTH, 20):
            c = side_color((x0 + 10) / WIDTH)
            pygame.draw.line(self.hud_top, c, (x0, HUD_TOP - 2), (x0 + 20, HUD_TOP - 2), 2)
            pygame.draw.line(self.hud_dock, c, (x0, 0), (x0 + 20, 0), 2)
        self.dim = pygame.Surface((WIDTH, DOCK_Y), pygame.SRCALPHA)
        self.flash_surf = pygame.Surface((WIDTH, HEIGHT))
        self.shake_buf = pygame.Surface((WIDTH, HEIGHT))
        self.panel_cache = {}

        # Fighters (built once; reset between rounds)
        self.player = CyberFighter(PLAYER_X, GROUND_Y, "player", self.gone_sprite)
        self.enemy = CyberFighter(ENEMY_X, GROUND_Y, "enemy", self.raone_sprite)

        self.ppo_model = None
        self.ppo_decision_acc = 0.0
        self.raone_attack_cd = 0.0
        self.raone_shield_cd = 0.0
        self.raone_heal_cd = 0.0
        self.raone_special_cd = 0.0
        # Prefer the newly trained Advanced v2 policy when it exists.
        # Until training is complete, safely fall back to the known aggressive model.
        ppo_candidates = [
            os.path.join(
                "raone_ppo_starter 2", "models", "advanced_v2",
                "best", "best_model.zip"
            ),
            os.path.join(
                "raone_ppo_starter 2", "models", "aggressive",
                "best", "best_model.zip"
            ),
        ]
        self.ppo_model_path = next((p for p in ppo_candidates if os.path.isfile(p)), None)
        if self.ppo_model_path:
            try:
                self.ppo_model = PPO.load(self.ppo_model_path, device="cpu")
                print(f"PPO RA.ONE loaded: {self.ppo_model_path}")
            except Exception as exc:
                self.ppo_model = None
                print(f"WARNING: PPO load failed; timer enemy remains active: {exc}")
        else:
            print("WARNING: No PPO checkpoint found; timer enemy remains active.")

        # ---------------- ML model ----------------
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                f"Model not found at {MODEL_PATH}. Run train_model.py first, "
                "and launch game.py from your project root."
            )
        self.model = joblib.load(MODEL_PATH)
        print("Model loaded successfully.")
        print("Model classes:", list(self.model.classes_))
        unknown = [str(c) for c in self.model.classes_ if str(c) not in ACTION_LABELS]
        if unknown:
            print(f"WARNING: model classes {unknown} are not game actions {list(ACTION_LABELS)}; "
                  f"those gestures will be shown but will not trigger anything.")
        missing = [a for a in ACTION_LABELS if a not in [str(c) for c in self.model.classes_]]
        if missing:
            print(f"WARNING: actions {missing} have no matching model class and can never trigger.")

        # ---------------- MediaPipe + camera ----------------
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            model_complexity=0,
            min_detection_confidence=0.55,
            min_tracking_confidence=0.55,
        )
        self.cap = None
        try:
            self.cap = cv2.VideoCapture(CAMERA_INDEX)
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: webcam initialisation raised {exc!r}.")
        self.camera_ok = bool(self.cap is not None and self.cap.isOpened())
        if not self.camera_ok:
            print("WARNING: Webcam could not be opened. Game will run, but gestures won't work.")
        self.camera_status = "LIVE" if self.camera_ok else "OFFLINE"

        self.history = deque(maxlen=STABLE_FRAMES)
        self.last_triggered_gesture = None
        self.last_action_time = 0
        self.last_action = None
        self.predicted_action = "—"
        self.predicted_confidence = 0.0
        self.raw_gesture = "No hand"
        self.hand_detected = False
        self.camera_surface = None
        self.camera_frame = None
        self.preview_size = (0, 0)
        self.landmark_points = []
        self.action_queue = deque()
        # Final Protocol uses the same webcam + MediaPipe + RandomForest pipeline.
        # Left hand controls movement; right hand controls combat gestures.
        self.final_protocol_input = {
            "movement_x": None, "movement_y": None,
            "action_gesture": None, "action_confidence": 0.0,
            "movement_hand": False, "action_hand": False,
            "camera_live": self.camera_ok,
        }
        self._cam_state = None
        self._cam_seen = -1
        self._cam_id = 0
        self._cam_fail = 0
        self._cam_stop = threading.Event()
        self._cam_thread = None
        self._closed = False
        if self.camera_ok and USE_CAMERA_THREAD:
            self._cam_thread = threading.Thread(target=self._camera_loop, name="camera", daemon=True)
            self._cam_thread.start()

        # ---------------- Effects / state ----------------
        self.particles = []
        self.floating_texts = []
        self.projectiles = []
        self.shockwaves = []
        self.flashes = []
        self.events = []
        self.screen_shake = 0
        self.hitstop = 0.0
        self.shake_mag = 6
        self.flash_alpha = 0
        self.flash_color = (255, 255, 255)
        self.combo = 0
        self.combo_timer = 0
        self.combo_pulse = 0.0
        self.total_actions = 0
        self.elapsed = 0
        self.heal_acc = 0.0
        self.charge_acc = 0.0
        self.enemy_charge_acc = 0.0
        self.celebrate_acc = 0.0
        self.fps_display = 0.0
        self.round_number = 1
        self.player_round_wins = 0
        self.enemy_round_wins = 0
        self.round_winner = ""

        # Final cinematic sequence is triggered after round 3.
        self.final_sequence_started = False
        self.state = "title"
        self.countdown = 0.0
        self.fight_banner = 0.0
        self.result_timer = 0.0
        self.motes = []
        rng = random.Random(5)
        for fx, col in ((PLAYER_X, (90, 200, 255)), (ENEMY_X, (255, 90, 100))):
            for _ in range(14):
                self.motes.append([fx + rng.uniform(-190, 190), rng.uniform(GROUND_Y - 460, GROUND_Y),
                                   rng.uniform(10, 32), rng.uniform(0, 6), col, fx])
        for _ in range(26):
            x = rng.uniform(0, WIDTH)
            self.motes.append([x, rng.uniform(150, GROUND_Y), rng.uniform(5, 14), rng.uniform(0, 6),
                               side_color(x / WIDTH), None])
        self.reset_game()
        self.state = "title"

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------
    def reset_game(self):
        self.ppo_decision_acc = 0.0
        self.raone_attack_cd = 0.0
        self.raone_shield_cd = 0.0
        self.raone_heal_cd = 0.0
        self.raone_special_cd = 0.0
        self.player_hp = self.current_player_max_hp()
        self.enemy_hp = self.current_enemy_max_hp()
        self.player_hp_disp = float(self.current_player_max_hp())
        self.enemy_hp_disp = float(self.current_enemy_max_hp())
        self.player_hp_ghost = float(self.current_player_max_hp())
        self.enemy_hp_ghost = float(self.current_enemy_max_hp())
        self.player.reset()
        self.enemy.reset()
        self.round_start = time.time()
        self.round_time = 0.0
        self.enemy_next_attack = time.time() + random.uniform(ENEMY_ATTACK_MIN, ENEMY_ATTACK_MAX)
        self.game_over = False
        self.winner = ""
        self.result_timer = 0.0
        self.combo = 0
        self.combo_timer = 0
        self.combo_pulse = 0.0
        self.total_actions = 0
        self.last_action = None
        self.last_action_time = 0
        self.predicted_action = "—"
        self.screen_shake = 0
        self.flash_alpha = 0
        self.fight_banner = 0.0
        self.particles.clear()
        self.floating_texts.clear()
        self.projectiles.clear()
        self.shockwaves.clear()
        self.flashes.clear()
        self.events.clear()
        self.action_queue.clear()
        self.history.clear()
        self.last_triggered_gesture = None

    def restart(self):
        """R key: start a fresh best-of-three match."""
        self.round_number = 1
        self.player_round_wins = 0
        self.enemy_round_wins = 0
        self.round_winner = ""
        self.final_sequence_started = False
        self.reset_game()
        self.restore_main_music()   # fresh match: song restarts from the beginning once
        self.begin_countdown()

    def current_round_duration(self):
        return ROUND_DURATIONS.get(self.round_number, ROUND_SECONDS)

    def current_player_max_hp(self):
        return PLAYER_ROUND_HP.get(self.round_number, PLAYER_MAX_HP)

    def current_enemy_max_hp(self):
        return ENEMY_ROUND_HP.get(self.round_number, ENEMY_MAX_HP)

    def _start_next_round(self):
        """Reset combat state while preserving the match score."""
        self.player_hp = self.current_player_max_hp()
        self.enemy_hp = self.current_enemy_max_hp()
        self.player_hp_disp = float(self.current_player_max_hp())
        self.enemy_hp_disp = float(self.current_enemy_max_hp())
        self.player_hp_ghost = float(self.current_player_max_hp())
        self.enemy_hp_ghost = float(self.current_enemy_max_hp())

        self.player.reset()
        self.enemy.reset()
        self.round_time = 0.0
        self.round_start = time.time()
        self.enemy_next_attack = time.time() + random.uniform(
            ENEMY_ATTACK_MIN, ENEMY_ATTACK_MAX
        )

        self.ppo_decision_acc = 0.0
        self.raone_attack_cd = 0.0
        self.raone_shield_cd = 0.0
        self.raone_heal_cd = 0.0
        self.raone_special_cd = 0.0
        self.combo = 0
        self.combo_timer = 0
        self.combo_pulse = 0.0
        self.game_over = False
        self.winner = ""
        self.result_timer = 0.0
        self.events.clear()
        self.projectiles.clear()
        self.particles.clear()
        self.floating_texts.clear()
        self.shockwaves.clear()
        self.flashes.clear()
        self.action_queue.clear()
        self.history.clear()
        self.last_triggered_gesture = None

    def restore_main_music(self):
        """Restore the regular soundtrack when restarting the main match."""
        try:
            if pygame.mixer.get_init() and self.music_loaded and os.path.isfile(self.main_music_path):
                pygame.mixer.music.stop()
                pygame.mixer.music.load(self.main_music_path)
                pygame.mixer.music.set_volume(self.main_music_volume)
                pygame.mixer.music.play(-1)
        except pygame.error as exc:
            print(f"WARNING: Could not restore background music: {exc}")

    def begin_countdown(self):
        self.state = "countdown"
        self.countdown = 0.0

    def begin_fight(self):
        self.state = "playing"
        if self.music_loaded:
            try:
                # Start the soundtrack only if nothing is playing (Round 1 / first fight).
                # Rounds 2 and 3 must NOT touch it, so the song keeps playing without restarting.
                if not pygame.mixer.music.get_busy():
                    pygame.mixer.music.load(self.main_music_path)
                    pygame.mixer.music.set_volume(self.main_music_volume)
                    pygame.mixer.music.play(-1)
            except pygame.error as exc:
                print(f"WARNING: Background music playback failed: {exc}")
        self.fight_banner = 0.9
        self.round_start = time.time()
        self.round_time = 0.0
        self.enemy_next_attack = time.time() + random.uniform(ENEMY_ATTACK_MIN, ENEMY_ATTACK_MAX)
        self.history.clear()
        self.last_triggered_gesture = None
        self.action_queue.clear()
        self.flash_screen((120, 190, 255), 70)

    def schedule(self, delay, fn):
        self.events.append([delay, fn])

    # ------------------------------------------------------------------
    # Effect helpers
    # ------------------------------------------------------------------
    def burst(self, x, y, color, count=28, speed=(80, 360), life=None, radius=None, gravity=90.0):
        for _ in range(count):
            a = random.uniform(0, math.tau)
            s = random.uniform(*speed)
            self.particles.append(Particle(x, y, color, (math.cos(a) * s, math.sin(a) * s),
                                           life=(random.uniform(*life) if life else None),
                                           radius=radius, gravity=gravity))
        over = len(self.particles) - MAX_PARTICLES
        if over > 0:
            del self.particles[:over]

    def floating(self, x, y, text, color, size=28):
        self.floating_texts.append(FloatingText(x, y, text, color, size))
        over = len(self.floating_texts) - MAX_FLOATING
        if over > 0:
            del self.floating_texts[:over]

    def flash_screen(self, color, alpha):
        self.flash_color = color
        self.flash_alpha = max(self.flash_alpha, alpha)

    def shake(self, seconds, mag=6):
        self.screen_shake = max(self.screen_shake, seconds)
        self.shake_mag = mag
        self.shake_total = max(seconds, 0.01)

    def add_flash(self, x, y, radius, color, life=0.3):
        self.flashes.append(ImpactFlash(x, y, radius, color, life))
        if len(self.flashes) > 12:
            del self.flashes[0]

    def add_ring(self, x, y, color, max_r=220, life=0.55, delay=0.0):
        self.shockwaves.append(Shockwave(x, y, color, max_r, life, delay))
        if len(self.shockwaves) > 14:
            del self.shockwaves[0]

    # ------------------------------------------------------------------
    # Player / enemy actions
    # ------------------------------------------------------------------
    def handle_action(self, action):
        now = time.time()
        if self.state != "playing" or self.game_over or now - self.last_action_time < ACTION_COOLDOWN:
            return
        self.last_action_time = now
        self.last_action = action
        self.total_actions += 1
        self.player.trigger(action)
        self.predicted_action = action.upper()
        px, py = self.player.body_pos()
        if action == "attack":
            self.floating(px + 120, py - 150, "ATTACK!", COLORS["blue_bright"], 32)
            self.schedule(0.10, lambda: self.launch_player_bolt("attack"))
            self.combo += 1
            self.combo_timer = COMBO_WINDOW
            self.combo_pulse = 1.0
            self.flash_screen(COLORS["blue_bright"], 26)
        elif action == "shield":
            self.burst(px, py, COLORS["cyan"], 26, speed=(120, 320), life=(0.3, 0.7))
            self.add_ring(px, py + 20, COLORS["cyan"], max_r=250, life=0.5)
            self.add_ring(px, py + 20, (255, 255, 255), max_r=170, life=0.35, delay=0.05)
            self.flash_screen(COLORS["cyan"], 30)
            self.floating(px, py - 250, "SHIELD UP", COLORS["cyan"], 30)
        elif action == "heal":
            before = self.player_hp
            self.player_hp = min(self.current_player_max_hp(), self.player_hp + HEAL_AMOUNT)
            healed = self.player_hp - before
            self.burst(px, py + 60, COLORS["green"], 20, speed=(40, 160), life=(0.5, 1.0), gravity=-60)
            self.add_ring(px, GROUND_Y, COLORS["green"], max_r=190, life=0.6)
            self.flash_screen(COLORS["green"], 24)
            self.floating(px, py - 200, f"+{healed} HP" if healed > 0 else "HP FULL", COLORS["green"], 34)
        elif action == "special":
            self.floating(px + 130, py - 190, "SUPERPOWER!", COLORS["violet"], 36)
            self.schedule(SPECIAL_CHARGE, lambda: self.launch_player_bolt("special"))
            self.combo += 2
            self.combo_timer = COMBO_WINDOW
            self.combo_pulse = 1.0
            self.flash_screen((90, 130, 255), 45)

    def launch_player_bolt(self, kind):
        if self.game_over or self.state != "playing":
            return
        hx, hy = self.player.hand_pos()
        ex, ey = self.enemy.body_pos()
        tx, ty = self.enemy.x - 55, ey
        if kind == "attack":
            p = Projectile(hx, hy, tx, ty, (120, 200, 255), 1500, "attack", 1, ATTACK_DAMAGE, 12, (40, 120, 255))
            self.add_flash(hx, hy, 70, (120, 200, 255), 0.18)
        else:
            p = Projectile(hx, hy, tx, ty, (190, 220, 255), 1050, "special", 1, SPECIAL_DAMAGE, 30, (90, 110, 255))
            self.add_flash(hx, hy, 170, (150, 170, 255), 0.35)
            self.add_ring(hx, hy, (150, 190, 255), max_r=130, life=0.4)
        self.projectiles.append(p)
        if len(self.projectiles) > MAX_PROJECTILES:
            del self.projectiles[0]

    def launch_enemy_bolt(self, damage=ENEMY_DAMAGE, kind="enemy"):
        if self.game_over or self.state != "playing":
            return
        hx, hy = self.enemy.hand_pos()
        px, py = self.player.body_pos()
        special = kind == "enemy_special"
        color = (255, 90, 180) if special else (255, 150, 140)
        self.add_flash(hx, hy, 130 if special else 80, color, 0.25)
        self.projectiles.append(Projectile(
            hx, hy, self.player.x + 30, py, color, 1450 if special else 1550,
            kind, -1, damage, 24 if special else 14, (255, 40, 60)
        ))
        if len(self.projectiles) > MAX_PROJECTILES:
            del self.projectiles[0]

    def update_raone_ppo(self, dt):
        # The tactical boss controller must run even if the PPO checkpoint
        # cannot be loaded. PPO is an optional baseline decision-maker.
        self.ppo_decision_acc += dt
        self.raone_attack_cd = max(0.0, self.raone_attack_cd - dt)
        self.raone_shield_cd = max(0.0, self.raone_shield_cd - dt)
        self.raone_heal_cd = max(0.0, self.raone_heal_cd - dt)
        self.raone_special_cd = max(0.0, self.raone_special_cd - dt)
        round_pressure = ROUND_PRESSURE.get(self.round_number, 1.0)

        if self.ppo_decision_acc < 0.1:
            return
        self.ppo_decision_acc = 0.0

        # Observation order must match raone_env.py exactly.
        # Cooldown features use the same round-scaled durations as the actions below.
        # Feature 9 estimates the remaining G.ONE action window from recent player activity.
        since_action = time.time() - self.last_action_time if self.last_action_time else 0.0
        obs = np.asarray([
            self.enemy_hp / self.current_enemy_max_hp(),
            self.player_hp / self.current_player_max_hp(),
            min(1.0, self.enemy.shield_time / 2.4),
            min(1.0, self.player.shield_time / SHIELD_DURATION),
            min(1.0, self.raone_attack_cd / (0.9 * round_pressure)),
            min(1.0, self.raone_shield_cd / (5.0 * round_pressure)),
            min(1.0, self.raone_heal_cd / (7.0 * round_pressure)),
            min(1.0, self.raone_special_cd / (4.0 * round_pressure)),
            min(1.0, max(0.0, ENEMY_ATTACK_MAX - since_action) / ENEMY_ATTACK_MAX),
            max(0.0, min(1.0, (self.current_round_duration() - self.round_time) / self.current_round_duration())),
        ], dtype=np.float32)

        if self.ppo_model is not None:
            action, _ = self.ppo_model.predict(obs, deterministic=True)
            action = int(np.asarray(action).item())
        else:
            action = 0

        # Hybrid PPO + fast tactical combat controller.
        # PPO provides the baseline choice; urgent combat priorities override it.
        special_ready = self.raone_special_cd <= 0.0
        attack_ready = self.raone_attack_cd <= 0.0
        heal_ready = self.raone_heal_cd <= 0.0
        shield_ready = self.raone_shield_cd <= 0.0
        raone_hp_ratio = self.enemy_hp / max(1.0, self.current_enemy_max_hp())

        # Detect G.ONE projectiles travelling toward RA.ONE.
        incoming_projectile = any(
            getattr(p, "direction", 0) > 0 and p.x >= self.enemy.x - 520
            for p in self.projectiles
        )
        recent_player_attack = (
            self.last_action in ("attack", "special")
            and self.last_action_time > 0
            and time.time() - self.last_action_time < 0.9
        )

        # Deterministic boss priorities. Special is NOT blocked by G.ONE's shield:
        # it is a heavy boss move and should visibly fire whenever ready.
        if raone_hp_ratio <= 0.62 and heal_ready:
            action = 3
        elif shield_ready and (incoming_projectile or recent_player_attack):
            action = 2
        elif special_ready:
            action = 4
        elif attack_ready:
            action = 1

        labels = {
            0: ("WAIT", COLORS["muted"]),
            1: ("ATTACK", COLORS["blue_bright"]),
            2: ("SHIELD", COLORS["red"]),
            3: ("HEAL", COLORS["green"]),
            4: ("SPECIAL", COLORS["violet"]),
        }
        self.raone_action_label, self.raone_action_color = labels.get(
            action, ("WAIT", COLORS["muted"])
        )
        self.raone_action_until = time.time() + 1.2

        if action == 1 and self.raone_attack_cd <= 0:
            self.enemy.trigger("attack")
            self.schedule(0.08, lambda: self.launch_enemy_bolt(16, "enemy"))
            self.raone_attack_cd = 0.78 * round_pressure

        elif action == 2 and self.raone_shield_cd <= 0:
            self.enemy.trigger("shield")
            self.enemy.shield_time = 2.2
            self.raone_shield_cd = 7.5 * round_pressure
            x, y = self.enemy.body_pos()
            self.burst(x, y, COLORS["red"], 18, speed=(80, 240), life=(0.25, 0.55))
            self.add_ring(x, y, COLORS["red"], max_r=180, life=0.4)
            self.floating(x, y - 220, "RA.ONE SHIELD", COLORS["red"], 26)

        elif action == 3 and self.raone_heal_cd <= 0:
            before = self.enemy_hp
            self.enemy_hp = min(self.current_enemy_max_hp(), self.enemy_hp + 26)
            self.raone_heal_cd = 9.0 * round_pressure
            healed = self.enemy_hp - before
            if healed:
                self.enemy.trigger("heal")
                x, y = self.enemy.body_pos()
                self.burst(x, y, COLORS["green"], 18, speed=(40, 150), life=(0.4, 0.8), gravity=-50)
                self.add_ring(x, GROUND_Y, COLORS["green"], max_r=160, life=0.5)
                self.floating(x, y - 200, f"+{healed} HP", COLORS["green"], 30)

        elif action == 4 and self.raone_special_cd <= 0:
            self.enemy.trigger("special")
            self.schedule(0.18, lambda: self.launch_enemy_bolt(36, "enemy_special"))
            self.raone_special_cd = 6.5 * round_pressure
            self.raone_attack_cd = max(self.raone_attack_cd, 0.78 * round_pressure)
            x, y = self.enemy.body_pos()
            self.floating(x - 100, y - 190, "RA.ONE SUPERPOWER", COLORS["violet"], 27)

    def enemy_attack(self):
        now = time.time()
        if self.state != "playing" or self.game_over or now < self.enemy_next_attack:
            return
        self.enemy_next_attack = now + random.uniform(ENEMY_ATTACK_MIN, ENEMY_ATTACK_MAX)
        self.enemy.trigger("attack")
        self.schedule(0.12, self.launch_enemy_bolt)

    def enemy_charge_level(self):
        if self.state != "playing" or self.game_over:
            return 0.0
        remaining = self.enemy_next_attack - time.time()
        return clamp(1 - remaining / 0.8) if remaining < 0.8 else 0.0

    # ---------------- projectile resolution ----------------
    def update_projectiles(self, dt):
        for p in self.projectiles[:]:
            arrived = p.update(dt)
            if p.kind == "special" and random.random() < 0.9:
                self.particles.append(Particle(p.x, p.y + random.uniform(-14, 14), (150, 190, 255),
                                               (random.uniform(-80, 20), random.uniform(-60, 60)),
                                               life=0.4, radius=3, gravity=0))
            if p.direction < 0 and self.player.shield_time > 0 and p.x <= self.player.dome_edge_x():
                self.projectiles.remove(p)
                self.on_bolt_blocked(p)
            elif p.direction > 0 and self.enemy.shield_time > 0 and p.x >= self.enemy.x - 180:
                self.projectiles.remove(p)
                self.burst(p.x, p.y, COLORS["red"], 14, speed=(80, 300), life=(0.2, 0.45))
                self.add_flash(p.x, p.y, 80, COLORS["red"], 0.2)
                self.floating(p.x, p.y - 35, "BLOCKED!", COLORS["red"], 26)
            elif arrived:
                self.projectiles.remove(p)
                if p.direction < 0:
                    self.on_enemy_hit_player(p)
                else:
                    self.on_player_hit_enemy(p, big=(p.kind == "special"))

    def on_bolt_blocked(self, p):
        self.player.shield_hit = 0.3
        self.player.shield_hit_pos = (p.x, p.y)
        self.burst(p.x, p.y, COLORS["cyan"], 22, speed=(120, 420), life=(0.2, 0.5), gravity=120)
        self.add_flash(p.x, p.y, 90, (150, 240, 255), 0.25)
        self.add_ring(p.x, p.y, COLORS["cyan"], max_r=90, life=0.3)
        self.floating(p.x - 10, p.y - 40, "BLOCKED!", COLORS["cyan"], 30)
        self.enemy.hit_flash = max(self.enemy.hit_flash, 0.0)

    def on_enemy_hit_player(self, p):
        x, y = p.x, p.y
        if not self.game_over:
            damage = int(getattr(p, 'damage', ENEMY_DAMAGE))
            self.player_hp = max(0, self.player_hp - damage)
            self.combo = 0
            self.combo_timer = 0
        self.player.hit(0.30, 28)
        self.burst(x, y, COLORS["red"], 22, speed=(120, 460), life=(0.25, 0.6), gravity=160)
        self.add_flash(x, y, 130, (255, 90, 80), 0.3)
        self.add_ring(x, y, COLORS["red"], max_r=130, life=0.35)
        damage = int(getattr(p, "damage", ENEMY_DAMAGE))
        self.floating(x, y - 70, f"-{damage}", COLORS["red"], 36)
        if getattr(p, "kind", "") == "enemy_special":
            self.add_ring(x, y, COLORS["violet"], max_r=180, life=0.45)
        self.flash_screen(COLORS["red"], 40)
        self.shake(0.16, 5)
        if self.player_hp <= 0 and not self.game_over:
            self.end_battle("SYSTEM OVERRIDE")

    def on_player_hit_enemy(self, p, big):
        x, y = p.x, p.y
        dmg = p.damage
        if not self.game_over:
            self.enemy_hp = max(0, self.enemy_hp - dmg)
        if big:
            self.hitstop = max(self.hitstop, 0.075)
            self.enemy.hit(0.55, 52)
            self.add_flash(x, y, 330, (150, 170, 255), 0.5)
            self.burst(x, y, (190, 210, 255), 46, speed=(150, 700), life=(0.3, 0.9), gravity=140)
            self.burst(x, y, COLORS["violet"], 34, speed=(80, 460), life=(0.4, 1.0), gravity=60)
            self.add_ring(x, y, (120, 170, 255), max_r=330, life=0.65)
            self.add_ring(x, y, COLORS["violet"], max_r=250, life=0.55, delay=0.08)
            self.add_ring(x, GROUND_Y + 6, (140, 190, 255), max_r=300, life=0.7, delay=0.04)
            self.floating(x, y - 90, f"-{dmg} SPECIAL", COLORS["violet"], 36)
            self.flash_screen((130, 150, 255), 105)
            self.shake(0.32, 9)
        else:
            self.hitstop = max(self.hitstop, 0.045)
            self.enemy.hit(0.28, 26)
            self.add_flash(x, y, 150, (150, 215, 255), 0.28)
            self.burst(x, y, (190, 230, 255), 26, speed=(160, 560), life=(0.2, 0.55), gravity=200)
            self.burst(x, y, COLORS["blue_bright"], 14, speed=(60, 260), life=(0.3, 0.6))
            self.add_ring(x, y, COLORS["blue_bright"], max_r=120, life=0.32)
            self.floating(x, y - 70, f"-{dmg}", COLORS["blue_bright"], 36)
            self.flash_screen((120, 190, 255), 30)
            self.shake(0.1, 3)
        if self.enemy_hp <= 0 and not self.game_over:
            self.end_battle("YOU WIN")

    def end_battle(self, winner):
        """Finish one round and decide whether the match is complete."""
        if self.state != "playing":
            return

        self.round_winner = winner
        if winner == "YOU WIN":
            self.player_round_wins += 1
        elif winner == "SYSTEM OVERRIDE":
            self.enemy_round_wins += 1

        self.game_over = True
        self.result_timer = 0.0
        self.events.clear()
        self.action_queue.clear()

        if winner != "DRAW":
            loser = self.enemy if winner == "YOU WIN" else self.player
            loser.defeated = True
            loser.defeat_t = 0.0
            x, y = loser.body_pos()
            self.burst(
                x, y,
                COLORS["yellow"] if winner == "YOU WIN" else COLORS["red"],
                80, speed=(100, 600), life=(0.5, 1.4), gravity=80
            )
            self.add_ring(x, y, (255, 255, 255), max_r=420, life=0.8)
            self.add_flash(x, y, 300, (255, 255, 255), 0.5)
            self.flash_screen((255, 255, 255), 140)

        match_finished = (self.round_number >= 3)

        if match_finished:
            self.state = "final_sequence"
            self.game_over = False
            self.final_sequence_started = False
        else:
            self.winner = winner
            self.state = "round_break"


    # ------------------------------------------------------------------
    # Final cinematic sequence: 2s pause -> hacked screen -> two story videos -> boss fight
    # ------------------------------------------------------------------
    def run_final_protocol_sequence(self):
        """Run the post-round cinematic and bonus boss fight once; no MCQ."""
        if self.final_sequence_started:
            return
        self.final_sequence_started = True

        try:
            from cinematic_video import play_ending_video
            from final_protocol import FinalProtocolBonus

            project_dir = os.path.dirname(os.path.abspath(__file__))

            # Stop the main game's soundtrack before the transition.
            try:
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
            except pygame.error:
                pass

            # Give the Round 3 victory/defeat moment two seconds to land before
            # switching to the hacked-screen cinematic.
            pause_elapsed = 0.0
            while pause_elapsed < 2.0:
                dt = min(self.clock.tick(FPS) / 1000.0, 0.05)
                pause_elapsed += dt
                for event in pygame.event.get():
                    if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                        self.quit_requested = True
                        self.state = "ending"
                        return
                pygame.display.flip()

            # 1) Existing hacked screen.
            if not show_hacked_screen(self.screen, self.clock, duration=5.0):
                self.quit_requested = True
                self.state = "ending"
                return

            # 2) Play the two user-supplied videos in the requested order.
            # Place both MP4 files beside game.py, or override paths with env vars.
            story_videos = [
                ("AFTER_HACK_SCREEN_VIDEO", "after_hack_screen.mp4"),
                ("AFTER_VIDEO1_PATH", "after_video1.mp4"),
            ]
            for env_name, default_name in story_videos:
                configured = os.environ.get(env_name, "").strip()
                video_path = configured if configured and os.path.isabs(configured) else os.path.join(
                    project_dir, configured or default_name
                )
                if not os.path.isfile(video_path):
                    raise FileNotFoundError(
                        f"Required transition video not found: {video_path}. "
                        f"Place {default_name} beside game.py or set {env_name}."
                    )
                print(f"Playing post-Round-3 cinematic: {video_path}")
                if not play_ending_video(self.screen, self.clock, video_path):
                    self.quit_requested = True
                    self.state = "ending"
                    return

            # Final Protocol boss fight (the post-round bonus mission).
            bonus = FinalProtocolBonus(self.screen, self.clock, ppo_model=self.ppo_model, camera_input_provider=self.get_final_protocol_input)
            result = bonus.run()
            self.quit_requested = bool(getattr(bonus, "exit_requested", False))

            # After the boss/bonus round, show only the first six seconds of
            # coub-vj2bc.mp4, looping until ESC. Do not show the normal result UI.
            if not self.quit_requested:
                project_dir = os.path.dirname(os.path.abspath(__file__))
                ending_candidates = ("coub-yj2bc.mp4", "coub-vj2bc.mp4")
                ending_video = next(
                    (os.path.join(project_dir, name) for name in ending_candidates
                     if os.path.isfile(os.path.join(project_dir, name))),
                    None,
                )
                if ending_video:
                    print(f"Playing final video: {ending_video}")
                    play_looping_ending_video(
                        self.screen, self.clock, ending_video, clip_seconds=6.0
                    )
                else:
                    print(f"Final video not found. Expected one of: {ending_candidates}")

                # After ESC exits the video, display the final quote poster, then quit.
                poster_path = os.path.join(
                    project_dir, "Futuristic Superhero Quote_ The End.png"
                )
                if os.path.isfile(poster_path):
                    try:
                        poster = pygame.image.load(poster_path).convert()
                        poster = pygame.transform.smoothscale(poster, self.screen.get_size())
                        pygame.mixer.music.stop()
                        self.screen.blit(poster, (0, 0))
                        pygame.display.set_caption("AI SUPERPOWERS // THE END")
                        pygame.display.flip()
                        poster_start = pygame.time.get_ticks()
                        show_poster = True
                        while show_poster and pygame.time.get_ticks() - poster_start < 6000:
                            for event in pygame.event.get():
                                if event.type == pygame.QUIT:
                                    show_poster = False
                                elif event.type == pygame.KEYDOWN:
                                    show_poster = False
                            self.clock.tick(FPS)
                    except (pygame.error, OSError) as exc:
                        print(f"Could not display final poster: {exc}")
                else:
                    print(f"Final poster not found: {poster_path}")

            # The cinematic is the end of the game.
            self.quit_requested = True
            self.game_over = False
            self.state = "ending"
        except (ImportError, FileNotFoundError, RuntimeError, pygame.error) as exc:
            print(f"Final Protocol sequence could not fully start: {exc}")
            # Fail gracefully to the main game's results screen rather than crash.
            self.winner = self.round_winner or "SYSTEM OVERRIDE"
            self.game_over = True
            self.result_timer = 0.0
            self.state = "over"

    # ------------------------------------------------------------------
    # Camera / gesture pipeline (same logic as before)
    # ------------------------------------------------------------------
    def _step_camera(self):
        """Capture one frame, run the existing RandomForest, and expose two-hand controls."""
        cap = self.cap
        if cap is None or not cap.isOpened():
            return False
        ok, frame = cap.read()
        if not ok or frame is None:
            self._cam_fail += 1
            if self._cam_fail > 90:
                self.camera_status = "NO SIGNAL"
            return False
        self._cam_fail = 0
        self.camera_status = "LIVE"
        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        fh, fw = frame.shape[:2]
        result = self.hands.process(rgb)

        hands = list(result.multi_hand_landmarks or [])
        handedness = list(result.multi_handedness or [])
        labeled = {}
        for i, hand in enumerate(hands):
            label = None
            if i < len(handedness) and handedness[i].classification:
                label = handedness[i].classification[0].label
            if label in ("Left", "Right"):
                labeled[label] = hand

        movement_hand = labeled.get("Left")
        action_hand = labeled.get("Right")
        # A one-hand fallback preserves the original regular-round control behaviour.
        if len(hands) == 1 and action_hand is None:
            action_hand = hands[0]
        # If handedness metadata is unavailable, use image position as a deterministic fallback.
        if len(hands) >= 2 and (movement_hand is None or action_hand is None):
            ordered = sorted(hands, key=lambda h: h.landmark[0].x)
            movement_hand = movement_hand or ordered[0]
            action_hand = action_hand or ordered[-1]

        movement_x = movement_y = None
        if movement_hand is not None:
            movement_x = float(movement_hand.landmark[0].x)
            movement_y = float(movement_hand.landmark[0].y)

        def make_hand_preview(hand, accent):
            """Create a separate, landmark-overlaid crop for one detected hand."""
            if hand is None:
                return None
            points = [(int(lm.x * fw), int(lm.y * fh)) for lm in hand.landmark]
            xs, ys = [pt[0] for pt in points], [pt[1] for pt in points]
            bw, bh = max(xs) - min(xs), max(ys) - min(ys)
            cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
            crop_h = max(130, int(max(bh, bw) * 2.0))
            crop_w = int(crop_h * 1.55)
            x1, y1 = max(0, cx - crop_w // 2), max(0, cy - crop_h // 2)
            x2, y2 = min(fw, cx + crop_w // 2), min(fh, cy + crop_h // 2)
            # Expand at frame edges while preserving a useful 16:10-ish crop.
            crop = rgb[y1:y2, x1:x2].copy()
            if crop.size == 0:
                return None
            target_w, target_h = 220, 138
            preview = cv2.resize(crop, (target_w, target_h), interpolation=cv2.INTER_AREA)
            mapped = [(int((px - x1) * target_w / max(1, x2 - x1)),
                       int((py - y1) * target_h / max(1, y2 - y1))) for px, py in points]
            # MediaPipe hand connections; draw directly into the preview in the camera thread.
            try:
                connections = self.mp_hands.HAND_CONNECTIONS
            except Exception:
                connections = ()
            for a, b in connections:
                cv2.line(preview, mapped[a], mapped[b], accent, 2, cv2.LINE_AA)
            for px, py in mapped:
                cv2.circle(preview, (px, py), 3, (245, 250, 255), -1, cv2.LINE_AA)
            return preview

        left_preview = make_hand_preview(movement_hand, (55, 190, 255))
        right_preview = make_hand_preview(action_hand, (255, 65, 90))

        landmarks = []
        raw_gesture = "No hand"
        confidence_out = 0.0
        hand_found = action_hand is not None
        action_gesture = None
        if action_hand is not None:
            for lm in action_hand.landmark:
                landmarks.append((lm.x, lm.y))
            features = extract_features(action_hand)
            try:
                probs = self.model.predict_proba(features)[0]
                idx = int(np.argmax(probs))
                prediction = str(self.model.classes_[idx])
                confidence = float(probs[idx])
            except Exception:
                prediction = str(self.model.predict(features)[0])
                confidence = 1.0
            raw_gesture = prediction
            confidence_out = confidence
            threshold = 0.90 if prediction == "attack" else GESTURE_CONFIDENCE
            if confidence >= threshold and prediction in ACTION_LABELS:
                action_gesture = prediction
                self.history.append(prediction)
            else:
                self.history.clear()
                self.last_triggered_gesture = None
        else:
            self.history.clear()
            self.last_triggered_gesture = None

        if len(self.history) == STABLE_FRAMES and len(set(self.history)) == 1:
            stable = self.history[-1]
            if stable != self.last_triggered_gesture:
                self.action_queue.append(stable)
                self.last_triggered_gesture = stable

        fh, fw = frame.shape[:2]
        sc = min(150.0 / fw, 84.0 / fh)
        pw, ph = max(1, int(fw * sc)), max(1, int(fh * sc))
        preview = cv2.resize(rgb, (pw, ph), interpolation=cv2.INTER_AREA)
        self._cam_id += 1
        self._cam_state = {
            "id": self._cam_id, "preview": preview, "size": (pw, ph), "landmarks": landmarks,
            "raw": raw_gesture, "conf": confidence_out, "hand": hand_found,
            "movement_x": movement_x, "movement_y": movement_y,
            "action_gesture": action_gesture,
            "movement_hand": movement_hand is not None,
            "action_hand": action_hand is not None,
            "movement_preview": left_preview,
            "action_preview": right_preview,
        }
        return True

    def _camera_loop(self):
        while not self._cam_stop.is_set():
            try:
                if not self._step_camera():
                    time.sleep(0.01)
            except Exception as exc:  # noqa: BLE001
                print(f"Camera thread error: {exc!r}")
                time.sleep(0.05)

    def read_camera(self):
        """Poll the latest camera/gesture result and fire any confirmed gesture actions."""
        if self._cam_thread is None and self.camera_ok and not self._cam_stop.is_set():
            try:
                self._step_camera()
            except Exception as exc:  # noqa: BLE001
                print(f"Camera error: {exc!r}")
        state = self._cam_state
        if state is not None and self._cam_stop.is_set():
            state = None
        if state is None:
            self.landmark_points = []
            self.hand_detected = False
            self.raw_gesture = "No hand"
            self.predicted_confidence = 0.0
            self.final_protocol_input = {
                "movement_x": None, "movement_y": None,
                "action_gesture": None, "action_confidence": 0.0,
                "movement_hand": False, "action_hand": False,
                "movement_preview": None, "action_preview": None, "camera_frame_id": -1,
                "camera_live": self.camera_status == "LIVE",
            }
        else:
            self.landmark_points = state["landmarks"]
            self.hand_detected = state["hand"]
            self.raw_gesture = state["raw"]
            self.predicted_confidence = state["conf"]
            self.final_protocol_input = {
                "movement_x": state.get("movement_x"),
                "movement_y": state.get("movement_y"),
                "action_gesture": state.get("action_gesture"),
                "action_confidence": state.get("conf", 0.0),
                "movement_hand": state.get("movement_hand", False),
                "action_hand": state.get("action_hand", False),
                "movement_preview": state.get("movement_preview"),
                "action_preview": state.get("action_preview"),
                "camera_frame_id": state.get("id", -1),
                "camera_live": self.camera_status == "LIVE",
            }
            if state["id"] != self._cam_seen:
                self._cam_seen = state["id"]
                self.preview_size = state["size"]
                self.camera_surface = pygame.surfarray.make_surface(np.transpose(state["preview"], (1, 0, 2)))
        while self.action_queue:
            self.handle_action(self.action_queue.popleft())

    def get_final_protocol_input(self):
        """Return fresh webcam controls for the Final Protocol boss fight."""
        self.read_camera()
        return dict(self.final_protocol_input)

    def stop_camera(self):
        self._cam_stop.set()
        th = self._cam_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=1.5)
        self._cam_thread = None
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
        self.camera_ok = False
        self.camera_status = "OFFLINE"
        self.camera_surface = None
        self._cam_state = None
        self.landmark_points = []
        self.action_queue.clear()

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------
    def update_ambient(self, dt):
        for m in self.motes:
            m[1] -= m[2] * dt
            if m[1] < 150:
                m[1] = GROUND_Y - random.uniform(0, 40)
                if m[5] is not None:
                    m[0] = m[5] + random.uniform(-190, 190)

    def update_fx(self, dt):
        px, py = self.player.body_pos()
        ex, ey = self.enemy.body_pos()
        if self.player.heal_anim > 0:
            self.heal_acc += dt * 85
            while self.heal_acc >= 1:
                self.heal_acc -= 1
                self.particles.append(Particle(px + random.uniform(-90, 90), GROUND_Y - random.uniform(0, 70),
                                               random.choice(((70, 255, 190), (80, 230, 255), (180, 255, 220))),
                                               (random.uniform(-14, 14), -random.uniform(80, 180)),
                                               life=random.uniform(0.7, 1.1), radius=random.randint(3, 5),
                                               gravity=-8, drag=1.0))
        cp = self.player.special_charge_progress()
        if cp > 0:
            self.charge_acc += dt * 160
            hx = px + self.player.facing * self.player.w * 0.30
            while self.charge_acc >= 1:
                self.charge_acc -= 1
                a = random.uniform(0, math.tau)
                r = random.uniform(150, 240)
                sx, sy = hx + math.cos(a) * r, py + math.sin(a) * r
                vx, vy = (hx - sx) / 0.35, (py - sy) / 0.35
                self.particles.append(Particle(sx, sy, random.choice(((120, 190, 255), (200, 210, 255), (255, 255, 255))),
                                               (vx, vy), life=0.33, radius=3, gravity=0, drag=1.0))
        ch = self.enemy_charge_level()
        if ch > 0:
            self.enemy_charge_acc += dt * 70 * ch
            hx = ex + self.enemy.facing * self.enemy.w * 0.36
            while self.enemy_charge_acc >= 1:
                self.enemy_charge_acc -= 1
                a = random.uniform(0, math.tau)
                r = random.uniform(70, 130)
                sx, sy = hx + math.cos(a) * r, py + math.sin(a) * r
                self.particles.append(Particle(sx, sy, random.choice(((255, 60, 70), (255, 140, 100))),
                                               ((hx - sx) / 0.3, (py - sy) / 0.3), life=0.28, radius=3,
                                               gravity=0, drag=1.0))
        if self.state == "over" and self.result_timer > KO_DELAY:
            self.celebrate_acc += dt * 55
            victory = self.winner == "YOU WIN"
            while self.celebrate_acc >= 1:
                self.celebrate_acc -= 1
                x = random.uniform(0, WIDTH)
                if victory:
                    col = random.choice(((90, 170, 255), (0, 238, 255), (210, 240, 255), (130, 120, 255)))
                    self.particles.append(Particle(x, random.uniform(-20, 40), col,
                                                   (random.uniform(-30, 30), random.uniform(40, 120)),
                                                   life=random.uniform(1.6, 2.6), radius=random.randint(3, 5),
                                                   gravity=25, drag=0.999))
                else:
                    col = random.choice(((255, 60, 60), (255, 130, 60), (200, 30, 50)))
                    self.particles.append(Particle(x, HEIGHT - random.uniform(0, 120), col,
                                                   (random.uniform(-20, 20), -random.uniform(60, 150)),
                                                   life=random.uniform(1.4, 2.4), radius=random.randint(2, 4),
                                                   gravity=-12, drag=0.999))

    def update(self, dt):
        if self.hitstop > 0:
            self.hitstop = max(0.0, self.hitstop - dt)
            dt = 0.0

        slow = 0.4 if (self.state == "over" and self.result_timer < KO_DELAY) else 1.0
        wdt = dt * slow

        if self.state == "countdown":
            self.countdown += dt
            if self.countdown >= COUNTDOWN_STEP * 3:
                self.begin_fight()
        elif self.state == "playing":
            self.round_time += dt
            if self.round_time >= self.current_round_duration():
                if self.player_hp > self.enemy_hp:
                    self.end_battle("YOU WIN")
                elif self.enemy_hp > self.player_hp:
                    self.end_battle("SYSTEM OVERRIDE")
                else:
                    self.end_battle("DRAW")
            else:
                self.update_raone_ppo(dt)
        elif self.state == "round_break":
            self.result_timer += dt
            if self.result_timer >= 2.5:
                self.round_number += 1
                self._start_next_round()
                self.begin_countdown()
        elif self.state == "over":
            self.result_timer += dt

        # scheduled events
        for ev in self.events[:]:
            ev[0] -= wdt
            if ev[0] <= 0:
                self.events.remove(ev)
                ev[1]()

        self.player.update(wdt)
        self.enemy.update(wdt)
        self.update_projectiles(wdt)
        self.update_fx(wdt)
        self.update_ambient(wdt)

        for p in self.particles:
            p.update(wdt)
        self.particles = [p for p in self.particles if p.life > 0]
        for f in self.floating_texts:
            f.update(wdt)
        self.floating_texts = [f for f in self.floating_texts if f.life > 0]
        for s in self.shockwaves:
            s.update(wdt)
        self.shockwaves = [s for s in self.shockwaves if s.life > 0]
        for f in self.flashes:
            f.update(wdt)
        self.flashes = [f for f in self.flashes if f.life > 0]

        self.combo_timer = max(0, self.combo_timer - dt)
        if self.combo_timer <= 0 and self.combo:
            self.combo = 0
        self.combo_pulse = max(0.0, self.combo_pulse - dt * 3.0)
        self.screen_shake = max(0, self.screen_shake - dt)
        self.flash_alpha = max(0, self.flash_alpha - 360 * dt)
        self.fight_banner = max(0.0, self.fight_banner - dt)

        # smooth health bars
        for side in ("player", "enemy"):
            hp = getattr(self, f"{side}_hp")
            disp = getattr(self, f"{side}_hp_disp")
            ghost = getattr(self, f"{side}_hp_ghost")
            disp += (hp - disp) * min(1.0, dt * 10)
            if abs(disp - hp) < 0.05:
                disp = float(hp)
            if ghost > disp:
                ghost += (disp - ghost) * min(1.0, dt * 2.0)
            else:
                ghost = disp
            setattr(self, f"{side}_hp_disp", disp)
            setattr(self, f"{side}_hp_ghost", ghost)

    # ------------------------------------------------------------------
    # Drawing: world
    # ------------------------------------------------------------------
    def draw_background(self, t):
        scr = self.screen
        scr.blit(self.bg_static, (0, 0))
        sway = math.sin(t * 0.18)
        scr.blit(self.far_layer, (-PARALLAX_PAD + int(sway * 6), HORIZON_Y - 300))
        scr.blit(self.near_layer, (-PARALLAX_PAD + int(sway * 16), HORIZON_Y - 260))
        off = int(t * 9) % WIDTH
        scr.blit(self.haze, (-off, HORIZON_Y - 105))
        scr.blit(self.haze, (WIDTH - off, HORIZON_Y - 105))
        # travelling light pulse on the floor
        q = (t * 0.32) % 1.0
        y = HORIZON_Y + int((HEIGHT - HORIZON_Y) * q ** 2.0)
        fade = 1 - q * 0.6
        for x0 in range(0, WIDTH, 40):
            pygame.draw.line(scr, scale_color(side_color((x0 + 20) / WIDTH), 0.9 * fade), (x0, y), (x0 + 40, y), 2)
        # drifting motes
        for m in self.motes:
            lvl = 0.35 + 0.35 * math.sin(t * 2 + m[3])
            draw_glow(scr, (m[0] + math.sin(t * 0.8 + m[3]) * 10, m[1]), 5, m[4], lvl)

    def draw_projectiles(self, dt=0):
        for p in self.projectiles:
            p.draw(self.screen)

    def draw_world(self, t):
        self.draw_background(t)
        self.enemy.draw(self.screen, t, charge=self.enemy_charge_level())
        self.player.draw(self.screen, t)
        self.draw_projectiles()
        for f in self.flashes:
            f.draw(self.screen)
        for s in self.shockwaves:
            s.draw(self.screen)
        for p in self.particles:
            p.draw(self.screen)
        for f in self.floating_texts:
            f.draw(self.screen, self.font)
        if self.screen_shake > 0:
            mag = self.shake_mag * clamp(self.screen_shake / getattr(self, "shake_total", 0.3))
            ox, oy = int(random.uniform(-mag, mag)), int(random.uniform(-mag * 0.6, mag * 0.6))
            if ox or oy:
                self.shake_buf.blit(self.screen, (0, 0))
                self.screen.blit(self.shake_buf, (ox, oy))

    # ------------------------------------------------------------------
    # Drawing: HUD
    # ------------------------------------------------------------------
    def panel(self, rect, border_color, fill=(9, 15, 34), radius=12):
        key = (rect.width, rect.height, tuple(border_color[:3]), radius)
        shadow = self.panel_cache.get(key)
        if shadow is None:
            shadow = pygame.Surface((rect.width + 12, rect.height + 12), pygame.SRCALPHA)
            pygame.draw.rect(shadow, (*border_color[:3], 28), shadow.get_rect(), border_radius=radius + 3)
            self.panel_cache[key] = shadow
        self.screen.blit(shadow, (rect.x - 6, rect.y - 6))
        pygame.draw.rect(self.screen, fill, rect, border_radius=radius)
        pygame.draw.rect(self.screen, border_color, rect, 2, border_radius=radius)

    def draw_health_bar(self, x, y, width, hp, max_hp, color, label, align_right=False,
                        display_hp=None, ghost_hp=None, sublabel=""):
        scr = self.screen
        h, s = 22, 14
        shown = hp if display_hp is None else display_hp
        ghost = shown if ghost_hp is None else ghost_hp
        inner = width - s

        def poly(x0, x1):
            if align_right:
                return [(x + x0, y), (x + x1, y), (x + x1 + s, y + h), (x + x0 + s, y + h)]
            return [(x + x0 + s, y), (x + x1 + s, y), (x + x1, y + h), (x + x0, y + h)]

        # nameplate
        name_font = get_font(34)
        if align_right:
            r = blit_text(scr, name_font, label, COLORS["white"], (x + width, y - 33), "topright")
            blit_text(scr, self.small, sublabel, color, (r.left - 10, y - 24), "topright")
        else:
            r = blit_text(scr, name_font, label, COLORS["white"], (x, y - 33), "topleft")
            blit_text(scr, self.small, sublabel, color, (r.right + 10, y - 24), "topleft")
        pygame.draw.polygon(scr, (12, 18, 42), poly(0, inner))
        for value, col in ((ghost, (255, 225, 120)), (shown, color)):
            fw = int(inner * clamp(value / max_hp))
            if fw > 0:
                if col is color and value / max_hp < 0.3:
                    col = mix_color(color, (255, 255, 255), 0.35 * (0.5 + 0.5 * math.sin(time.time() * 9)))
                pygame.draw.polygon(scr, col, poly(inner - fw, inner) if align_right else poly(0, fw))
        pygame.draw.polygon(scr, scale_color(color, 0.9), poly(0, inner), 2)
        blit_text(scr, self.small, f"{int(hp):03d} / {max_hp}", COLORS["white"],
                  (x + width if align_right else x, y + h + 6), "topright" if align_right else "topleft")

    def status_text(self):
        if self.state == "title":
            return "STANDBY", COLORS["muted"]
        if self.state == "countdown":
            return "GET READY", COLORS["yellow"]
        if self.state == "over":
            return "MATCH COMPLETE", COLORS["white"]
        if self.state == "round_break":
            return "ROUND COMPLETE", COLORS["yellow"]
        if self.enemy_charge_level() > 0:
            return "WARNING: RA.ONE CHARGING", COLORS["red"]
        if self.round_time >= self.current_round_duration():
            return "OVERTIME", COLORS["pink"]
        return "BATTLE IN PROGRESS", COLORS["cyan"]

    def draw_hud(self, t):
        scr = self.screen
        scr.blit(self.hud_top, (0, 0))
        blit_text(scr, get_font(15), "R RESTART  |  ESC QUIT  |  Q CAMERA OFF", COLORS["muted"], (28, 3))
        blit_text(scr, get_font(15), f"{self.fps_display:.0f} FPS", COLORS["muted"], (WIDTH - 28, 3), "topright")

        # title + timer plate
        tg = glow_text(self.title_font, "AI SUPERPOWERS", COLORS["cyan"], spread=7)
        scr.blit(tg, tg.get_rect(center=(WIDTH // 2, 17)))
        plate = [(520, 34), (680, 34), (696, 57), (680, 80), (520, 80), (504, 57)]
        pygame.draw.polygon(scr, (7, 12, 30), plate)
        pygame.draw.polygon(scr, COLORS["cyan"], plate, 2)
        timer = max(0, int(self.current_round_duration() - self.round_time))
        blit_text(scr, self.large, f"{timer // 60:02d}:{timer % 60:02d}", COLORS["white"], (WIDTH // 2, 57), "center")
        st, stc = self.status_text()
        score = f"G.ONE {self.player_round_wins} - {self.enemy_round_wins} RA.ONE"
        blit_text(scr, self.small, f"ROUND {self.round_number}/3  |  {score}  |  {st}", stc, (WIDTH // 2, 95), "center")

        # health bars
        self.draw_health_bar(28, 50, 430, self.player_hp, self.current_player_max_hp(), COLORS["cyan"], "G.ONE",
                             False, self.player_hp_disp, self.player_hp_ghost, "PLAYER")
        self.draw_health_bar(WIDTH - 28 - 430, 50, 430, self.enemy_hp, self.current_enemy_max_hp(), COLORS["red"], "RA.ONE",
                             True, self.enemy_hp_disp, self.enemy_hp_ghost, "ENEMY")

        # combo counter
        if self.combo > 1 and self.combo_timer > 0:
            sc = 1.0 + 0.45 * ease_out_cubic(self.combo_pulse)
            img = glow_text(get_font(50), f"{self.combo}x COMBO", COLORS["yellow"], (255, 150, 40), spread=8)
            if abs(sc - 1.0) > 0.02:
                img = pygame.transform.smoothscale(img, (int(img.get_width() * sc), int(img.get_height() * sc)))
            scr.blit(img, (22, 118))
            bw = int(190 * self.combo_timer / COMBO_WINDOW)
            pygame.draw.rect(scr, COLORS["yellow"], (36, 172, bw, 4), border_radius=2)

        # Live PPO action indicator for RA.ONE
        panel = pygame.Rect(WIDTH - 310, 125, 280, 48)
        color = self.raone_action_color
        pygame.draw.rect(scr, (12, 10, 28), panel, border_radius=9)
        pygame.draw.rect(scr, color, panel, 2, border_radius=9)

        label = self.raone_action_label
        if self.state == "playing" and time.time() > self.raone_action_until:
            label = "WAIT / DECIDING"

        text_img = glow_text(
            get_font(25), f"RA.ONE AI: {label}", color, spread=3
        )
        scr.blit(text_img, text_img.get_rect(center=panel.center))

        self.draw_dock(t)

    def draw_dock(self, t):
        scr = self.screen
        scr.blit(self.hud_dock, (0, DOCK_Y))
        now = time.time()

        # --- webcam panel ---
        cam_color = COLORS["cyan"] if (self.camera_ok and self.camera_status == "LIVE") else COLORS["red"]
        cam_rect = pygame.Rect(14, DOCK_Y + 8, 170, 92)
        self.panel(cam_rect, cam_color)
        box = pygame.Rect(22, DOCK_Y + 12, 154, 84)
        pygame.draw.rect(scr, (4, 8, 20), box)
        if self.camera_surface is not None:
            pw, ph = self.preview_size
            bx, by = box.x + (box.width - pw) // 2, box.y + (box.height - ph) // 2
            scr.blit(self.camera_surface, (bx, by))
            if self.landmark_points:
                pts = [(bx + px * pw, by + py * ph) for px, py in self.landmark_points]
                for a, b in self.mp_hands.HAND_CONNECTIONS:
                    pygame.draw.line(scr, (30, 160, 120), pts[a], pts[b], 1)
                for p in pts:
                    pygame.draw.circle(scr, COLORS["green"], (int(p[0]), int(p[1])), 2)
            else:
                blit_text(scr, self.small, "NO HAND", COLORS["red"], (box.centerx, box.bottom - 6), "midbottom")
            pygame.draw.circle(scr, COLORS["red"], (box.x + 9, box.y + 9), 3)
            blit_text(scr, get_font(15), "LIVE", COLORS["white"], (box.x + 16, box.y + 3))
        else:
            blit_text(scr, self.small, "CAMERA OFFLINE", COLORS["red"], box.center, "center")
        pygame.draw.rect(scr, cam_color, box, 1)

        # --- ML status panel ---
        conf = clamp(self.predicted_confidence)
        known = self.raw_gesture in ACTION_LABELS
        accent = ACTION_COLORS.get(self.raw_gesture, COLORS["muted"]) if self.hand_detected else COLORS["red"]
        ml = pygame.Rect(196, DOCK_Y + 8, 330, 92)
        self.panel(ml, accent)
        blit_text(scr, self.small, "LIVE ML LINK", COLORS["cyan"], (ml.x + 14, ml.y + 9))
        if not self.camera_ok or self.camera_status != "LIVE":
            chip, chip_c = ("CAMERA OFFLINE" if not self.camera_ok else self.camera_status), COLORS["red"]
        elif not self.hand_detected:
            chip, chip_c = "NO HAND DETECTED", (COLORS["red"] if int(t * 2) % 2 == 0 else COLORS["yellow"])
        elif len(self.history) >= STABLE_FRAMES:
            chip, chip_c = "GESTURE LOCKED", COLORS["green"]
        else:
            chip, chip_c = "TRACKING", COLORS["cyan"]
        blit_text(scr, self.small, chip, chip_c, (ml.right - 14, ml.y + 9), "topright")
        if self.hand_detected:
            gname = GESTURE_NAMES.get(self.raw_gesture, self.raw_gesture.upper())
            arrow = f"  >  {ACTION_LABELS[self.raw_gesture]}" if known else ""
            blit_text(scr, self.medium, gname + arrow, COLORS["white"], (ml.x + 14, ml.y + 28))
        else:
            blit_text(scr, self.medium, "NO HAND", COLORS["muted"], (ml.x + 14, ml.y + 28))
        blit_text(scr, self.small, f"CONFIDENCE  {conf * 100:.1f}%", COLORS["cyan"], (ml.x + 14, ml.y + 54))
        pygame.draw.rect(scr, (28, 40, 61), (ml.x + 168, ml.y + 57, 148, 9), border_radius=4)
        if conf > 0:
            pygame.draw.rect(scr, COLORS["green"] if conf > 0.75 else COLORS["yellow"],
                             (ml.x + 168, ml.y + 57, int(148 * conf), 9), border_radius=4)
        pygame.draw.line(scr, (20, 80, 110), (ml.x + 168 + int(148 * GESTURE_CONFIDENCE), ml.y + 54),
                         (ml.x + 168 + int(148 * GESTURE_CONFIDENCE), ml.y + 69), 1)
        # lock progress + triggered action
        for i in range(STABLE_FRAMES):
            c = COLORS["green"] if i < len(self.history) else (28, 40, 61)
            pygame.draw.rect(scr, c, (ml.x + 14 + i * 14, ml.y + 76, 11, 6), border_radius=2)
        age = now - self.last_action_time if self.last_action else 99
        if self.last_action and age < 2.0:
            blit_text(scr, self.small, f"TRIGGERED: {ACTION_LABELS[self.last_action]}",
                      scale_color(ACTION_COLORS[self.last_action], 1.0 - age / 3.0), (ml.right - 14, ml.y + 72), "topright")
        else:
            blit_text(scr, self.small, "TRIGGERED: --", COLORS["muted"], (ml.right - 14, ml.y + 72), "topright")

        # --- power cards ---
        cd_remaining = max(0.0, ACTION_COOLDOWN - (now - self.last_action_time)) if self.last_action else 0.0
        playing = self.state == "playing"
        for i, action in enumerate(ACTION_ORDER):
            color = ACTION_COLORS[action]
            r = pygame.Rect(538 + i * 162, DOCK_Y + 8, 156, 92)
            active = self.last_action == action and (now - self.last_action_time) < 0.6
            if action == "shield" and self.player.shield_time > 0:
                state_txt, frac, bar_c = f"ACTIVE {self.player.shield_time:.1f}s", self.player.shield_time / SHIELD_DURATION, color
            elif not playing:
                state_txt, frac, bar_c = "STANDBY", 0.0, COLORS["muted"]
            elif cd_remaining > 0:
                state_txt, frac, bar_c = "COOLDOWN", 1 - cd_remaining / ACTION_COOLDOWN, COLORS["yellow"]
            else:
                state_txt, frac, bar_c = "READY", 1.0, COLORS["green"]
            self.panel(r, color if (active or state_txt == "READY") else scale_color(color, 0.55), (10, 15, 34), 9)
            if active:
                draw_glow(scr, r.center, 90, color, 0.25)
            pygame.draw.circle(scr, color, (r.x + 24, r.y + 28), 15, 2)
            blit_text(scr, self.medium, GESTURE_ICONS[action], color, (r.x + 24, r.y + 28), "center")
            blit_text(scr, self.small, GESTURE_NAMES[action], COLORS["muted"], (r.x + 48, r.y + 12))
            blit_text(scr, get_font(26), ACTION_LABELS[action], COLORS["white"], (r.x + 48, r.y + 28))
            blit_text(scr, self.small, ACTION_DETAIL[action], color, (r.x + 12, r.y + 52))
            pygame.draw.rect(scr, (28, 40, 61), (r.x + 12, r.y + 72, r.width - 24, 6), border_radius=3)
            if frac > 0:
                pygame.draw.rect(scr, bar_c, (r.x + 12, r.y + 72, int((r.width - 24) * frac), 6), border_radius=3)
            blit_text(scr, get_font(15), state_txt, bar_c, (r.right - 12, r.y + 54), "topright")

    # ------------------------------------------------------------------
    # Drawing: overlays
    # ------------------------------------------------------------------
    def draw_title_screen(self, t):
        scr = self.screen
        self.dim.fill((2, 5, 16, 150))
        scr.blit(self.dim, (0, 0))
        big = get_font(104)
        g = glow_text(big, "G.ONE", COLORS["blue_bright"], spread=14)
        v = glow_text(get_font(66), "VS", COLORS["white"], (255, 255, 255), spread=10)
        r = glow_text(big, "RA.ONE", COLORS["red"], spread=14)
        scr.blit(g, g.get_rect(midright=(WIDTH // 2 - 55, 160)))
        scr.blit(v, v.get_rect(center=(WIDTH // 2, 160)))
        scr.blit(r, r.get_rect(midleft=(WIDTH // 2 + 55, 160)))

        pulse = 0.5 + 0.5 * math.sin(t * 3.2)
        btn = pygame.Rect(WIDTH // 2 - 130, 300, 260, 58)
        pygame.draw.rect(scr, (8, 16, 40), btn, border_radius=10)
        pygame.draw.rect(scr, mix_color(COLORS["cyan"], (255, 255, 255), pulse * 0.5), btn, 3, border_radius=10)
        draw_glow(scr, btn.center, 150, COLORS["cyan"], 0.12 + 0.12 * pulse)
        blit_text(scr, get_font(40), "START BATTLE", COLORS["white"], btn.center, "center")
        if int(t * 2) % 2 == 0:
            blit_text(scr, self.small, "PRESS ENTER OR SPACE", COLORS["cyan"], (WIDTH // 2, 376), "center")

        blit_text(scr, self.small, "HAND GESTURES", COLORS["muted"], (WIDTH // 2, 410), "center")
        for i, action in enumerate(ACTION_ORDER):
            row = pygame.Rect(WIDTH // 2 - 130, 428 + i * 44, 260, 38)
            col = ACTION_COLORS[action]
            pygame.draw.rect(scr, (8, 14, 34), row, border_radius=8)
            pygame.draw.rect(scr, scale_color(col, 0.8), row, 2, border_radius=8)
            pygame.draw.circle(scr, col, (row.x + 22, row.centery), 12, 2)
            blit_text(scr, self.font, GESTURE_ICONS[action], col, (row.x + 22, row.centery), "center")
            blit_text(scr, self.small, GESTURE_NAMES[action], COLORS["white"], (row.x + 44, row.y + 5))
            blit_text(scr, get_font(16), ACTION_DETAIL[action], col, (row.x + 44, row.y + 21))
            blit_text(scr, self.small, ACTION_LABELS[action], col, (row.right - 12, row.centery), "midright")

    def draw_countdown(self, t):
        scr = self.screen
        self.dim.fill((2, 5, 16, 70))
        scr.blit(self.dim, (0, 0))
        step = min(2, int(self.countdown / COUNTDOWN_STEP))
        local = (self.countdown % COUNTDOWN_STEP) / COUNTDOWN_STEP
        label = str(3 - step)
        col = (COLORS["blue_bright"], COLORS["violet"], COLORS["red"])[step]
        self.draw_big_banner(label, col, local, 250)

    def draw_big_banner(self, text, color, local, size):
        img = glow_text(get_font(size), text, color, spread=16)
        sc = 1.0 + 0.7 * (1 - ease_out_cubic(local * 2.2))
        w, h = int(img.get_width() * sc), int(img.get_height() * sc)
        scaled = pygame.transform.smoothscale(img, (max(2, w), max(2, h)))
        scaled.set_alpha(int(255 * clamp(1.4 - local * 0.8)))
        self.screen.blit(scaled, scaled.get_rect(center=(WIDTH // 2, 330)))

    def draw_round_break(self):
        scr = self.screen
        shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        shade.fill((3, 8, 25, 205))
        scr.blit(shade, (0, 0))

        if self.round_winner == "YOU WIN":
            title, color = "G.ONE WINS ROUND", COLORS["cyan"]
        elif self.round_winner == "SYSTEM OVERRIDE":
            title, color = "RA.ONE WINS ROUND", COLORS["red"]
        else:
            title, color = "ROUND DRAW", COLORS["yellow"]

        blit_text(scr, get_font(64), title, color, (WIDTH // 2, 235), "center")
        blit_text(
            scr, get_font(32),
            f"ROUND SCORE  {self.player_round_wins} - {self.enemy_round_wins}",
            COLORS["white"], (WIDTH // 2, 310), "center"
        )
        blit_text(
            scr, get_font(25), "NEXT ROUND STARTING...",
            COLORS["muted"], (WIDTH // 2, 375), "center"
        )

    def draw_game_over(self):
        scr = self.screen
        t = time.time()
        if self.final_sequence_started:
            # Final Protocol result: subtle top indicator only, no full-screen overlay.
            victory = self.winner == "YOU WIN"
            blit_text(scr, get_font(30), "VICTORY" if victory else "DEFEAT",
                      COLORS["blue_bright"] if victory else COLORS["red"],
                      (WIDTH // 2, 30), "center")
            blit_text(scr, self.font, "R: REMATCH   ESC: EXIT", COLORS["muted"],
                      (WIDTH // 2, 58), "center")
            return
        if self.result_timer < KO_DELAY:
            k = self.result_timer / KO_DELAY
            img = glow_text(get_font(200), "K.O.", (255, 255, 255), (255, 120, 90), spread=14)
            sc = 1.0 + 0.25 * (1 - ease_out_cubic(k * 3))
            s = pygame.transform.smoothscale(img, (int(img.get_width() * sc), int(img.get_height() * sc)))
            s.set_alpha(int(255 * clamp(1.6 - k * 1.2)))
            scr.blit(s, s.get_rect(center=(WIDTH // 2, 300)))
            return
        vis = clamp((self.result_timer - KO_DELAY) / 0.7)
        victory = self.winner == "YOU WIN"
        tied = self.winner == "DRAW"
        main = COLORS["yellow"] if tied else (COLORS["blue_bright"] if victory else COLORS["red"])
        accent = COLORS["yellow"] if tied else (COLORS["cyan"] if victory else COLORS["ember"])
        self.dim.fill((4, 10, 34, int(190 * vis)) if victory else (30, 3, 8, int(205 * vis)))
        scr.blit(self.dim, (0, 0))
        vig = self.vig_blue if victory else self.vig_red
        vig.set_alpha(int(255 * vis))
        scr.blit(vig, (0, 0))

        # glowing border frame with corner brackets
        pulse = 0.5 + 0.5 * math.sin(t * 3)
        fr = pygame.Rect(22, 22, WIDTH - 44, DOCK_Y - 40)
        for i, a in enumerate((0.25, 0.5, 1.0)):
            pygame.draw.rect(scr, scale_color(main, a * vis * (0.6 + 0.4 * pulse)), fr.inflate(-i * 6, -i * 6), 2, border_radius=6)
        L = 46
        for (cx, cy, sx, sy) in ((fr.left, fr.top, 1, 1), (fr.right, fr.top, -1, 1),
                                 (fr.left, fr.bottom, 1, -1), (fr.right, fr.bottom, -1, -1)):
            pygame.draw.line(scr, scale_color(accent, vis), (cx, cy), (cx + sx * L, cy), 4)
            pygame.draw.line(scr, scale_color(accent, vis), (cx, cy), (cx, cy + sy * L), 4)

        title = "MATCH DRAW" if tied else ("G.ONE WINS" if victory else "RA.ONE WINS")
        img = glow_text(get_font(150), title, main, (255, 255, 255) if victory else (255, 90, 60), spread=16)
        sc = (0.9 + 0.1 * ease_out_back(vis)) * (1 + 0.02 * math.sin(t * 3))
        s = pygame.transform.smoothscale(img, (int(img.get_width() * sc), int(img.get_height() * sc)))
        s.set_alpha(int(255 * vis))
        scr.blit(s, s.get_rect(center=(WIDTH // 2, 235)))
        sub = (f"FINAL SCORE  {self.player_round_wins} - {self.enemy_round_wins}"
               if tied else (f"MISSION COMPLETE  |  FINAL SCORE {self.player_round_wins}-{self.enemy_round_wins}"
               if victory else f"SYSTEM OVERRIDE  |  FINAL SCORE {self.player_round_wins}-{self.enemy_round_wins}"))
        sf = glow_text(get_font(36), sub, COLORS["white"], accent, spread=6)
        sf.set_alpha(int(255 * vis))
        scr.blit(sf, sf.get_rect(center=(WIDTH // 2, 340)))
        stats = f"ACTIONS {self.total_actions}   |   TIME {int(self.round_time)}s   |   G.ONE {self.player_hp} HP   |   RA.ONE {self.enemy_hp} HP"
        blit_text(scr, self.font, stats, COLORS["muted"], (WIDTH // 2, 385), "center")

        btn = pygame.Rect(WIDTH // 2 - 190, 430, 380, 64)
        pygame.draw.rect(scr, (8, 14, 36), btn, border_radius=12)
        pygame.draw.rect(scr, mix_color(accent, (255, 255, 255), pulse * 0.4), btn, 3, border_radius=12)
        draw_glow(scr, btn.center, 200, accent, (0.10 + 0.12 * pulse) * vis)
        blit_text(scr, get_font(40), "PRESS R TO REMATCH", COLORS["white"], btn.center, "center")
        blit_text(scr, self.font, "ESC TO EXIT", COLORS["muted"], (WIDTH // 2, 520), "center")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def tick(self):
        dt = min(self.clock.tick(FPS) / 1000.0, 0.04)
        self.fps_display = self.clock.get_fps()
        t = time.time()
        self.elapsed += dt

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return False
                elif event.key == pygame.K_r:
                    self.restart()
                elif event.key == pygame.K_q:
                    self.stop_camera()
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    if self.state == "title":
                        self.begin_countdown()

        self.read_camera()
        self.update(dt)

        self.draw_world(t)
        self.draw_hud(t)
        if self.state == "title":
            self.draw_title_screen(t)
        elif self.state == "countdown":
            self.draw_countdown(t)
        elif self.state == "playing" and self.fight_banner > 0:
            self.draw_big_banner("FIGHT!", COLORS["yellow"], 1 - self.fight_banner / 0.9, 190)
        if self.state == "round_break":
            self.draw_round_break()
        elif self.game_over and self.state == "over":
            self.draw_game_over()
        if self.flash_alpha > 0:
            self.flash_surf.fill(self.flash_color)
            self.flash_surf.set_alpha(int(min(255, self.flash_alpha)))
            self.screen.blit(self.flash_surf, (0, 0))
        pygame.display.flip()
        if self.state == "final_sequence":
            self.run_final_protocol_sequence()
            if getattr(self, "quit_requested", False):
                return False
        return True

    def run(self):
        try:
            while self.tick():
                pass
        finally:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stop_camera()
        try:
            self.hands.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except pygame.error:
            pass
        pygame.quit()


if __name__ == "__main__":
    game = None
    try:
        game = CyberpunkGame()
        game.run()
    except KeyboardInterrupt:
        print("\nGame stopped.")
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)
    finally:
        if game is not None:
            game.close()
        pygame.quit()