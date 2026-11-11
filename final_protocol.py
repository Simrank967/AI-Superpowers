"""
FINAL PROTOCOL — Cinematic G.ONE vs RA.ONE Boss Fight

Controls:
    A / D or LEFT / RIGHT : Move
    W or UP                : Jump
    J                      : Attack
    K or DOWN              : Block
    L                      : Special energy blast
    SPACE                  : Dash / evade
    ESC                    : Exit

Integration:
    from final_protocol import FinalProtocolBonus
    bonus = FinalProtocolBonus(screen, clock)
    result = bonus.run()  # "victory" or "defeat"
"""

import math
import random
from pathlib import Path

import pygame


# ============================================================
# ASSET DISCOVERY
# ============================================================

ROOT = Path(__file__).resolve().parent


def find_asset(*candidates):
    roots = (Path.cwd(), ROOT, ROOT.parent)

    for root in roots:
        for candidate in candidates:
            path = root / candidate
            if path.is_file():
                return path

    return None


GONE_PATH = find_asset(
    # Preferred current G.ONE full-body sprite
    "characters/gone1.png",
    "gone1.png",
    # Fallbacks
    "gone_bg.png",
    "assets/gone_bg.png",
    "Cinematic Blue-Energy Armored Hero.png",
)

RAONE_PATH = find_asset(
    # Preferred current RA.ONE full-body sprite
    "characters/raone1.png",
    "raone1.png",
    # Fallbacks
    "raone_bg.png",
    "assets/raone_bg.png",
    "Neon Shard Cyber Warrior.png",
)

# Playable Bonus Round roster. All fighters share the same combat stats/controls.
PLAYER_ROSTER = (
    ("G.ONE", ("characters/gone1.png", "characters/gone.png", "gone1.png", "gone.png")),
    ("BOSS CHEF", ("characters/boss_chef.png",)),
    ("CAT SUPERWOMAN", ("characters/cat_superwoman.png",)),
    ("SNAKE QUEEN", ("characters/snake_queen.png",)),
)

MUSIC_PATH = find_asset("Ra-One-Humming-Ringtone.mp3")

if MUSIC_PATH is None:
    MUSIC_PATH = find_asset("Raftaarein Ra One 128 Kbps.mp3")

# The background image is stored directly in the project root (next to game.py).
# Other locations are kept as fallbacks in case the project is reorganized later.
BG_IMAGE_PATH = find_asset(
    "BACKGROUND.png",
    "backgrounds/BACKGROUND.png",
    "assets/BACKGROUND.png",

    # Fallback to previous backgrounds
    "Gemini_Generated_Image_b11lcyb11lcyb11l.png",
    "backgrounds/Gemini_Generated_Image_b11lcyb11lcyb11l.png",
    "assets/Gemini_Generated_Image_b11lcyb11lcyb11l.png",
    "backgrounds/final_protocol_bg.png",
    "assets/final_protocol_bg.png",
    "final_protocol_bg.png",
    "backgrounds/apocalypse_city.png",
    "apocalypse_city.png",
)
# Fraction of the scaled image height where the fighters' feet should sit (road level).
BG_FEET_ANCHOR = 0.82
USE_IMAGE_BACKGROUND = False   # True = old BACKGROUND.png atmosphere instead of the procedural rooftop
FIGHT_SECONDS = 150            # boss-fight timer; at 0 the higher health bar wins
AURA_PAD = 64                  # glow margin around each fighter sprite


# ============================================================
# COLORS
# ============================================================

INK = (4, 6, 13)
DARK = (8, 12, 23)

BLUE = (47, 173, 255)
BLUE_WHITE = (188, 237, 255)

RED = (255, 48, 69)
RED_WHITE = (255, 171, 178)
GREEN = (50, 255, 170)

GOLD = (255, 201, 94)
WHITE = (238, 246, 255)
MUTED = (137, 156, 181)


def clamp(value, low, high):
    return max(low, min(high, value))


# ============================================================
# DRAWING HELPERS
# ============================================================

_FONT_CACHE = {}
_TEXT_CACHE = {}


def get_font(size, bold=False):
    key = (int(size), bool(bold))
    font = _FONT_CACHE.get(key)
    if font is None:
        font = pygame.font.SysFont("arial", int(size), bold=bold)
        _FONT_CACHE[key] = font
    return font


def draw_text(
    surface,
    text,
    size,
    color,
    position,
    center=True,
    bold=False,
    right=False,
):
    key = (str(text), int(size), tuple(color), bool(bold))
    image = _TEXT_CACHE.get(key)
    if image is None:
        if len(_TEXT_CACHE) > 500:
            _TEXT_CACHE.clear()
        image = get_font(size, bold).render(str(text), True, color)
        _TEXT_CACHE[key] = image
    rect = image.get_rect()

    if right:
        rect.topright = (int(position[0]), int(position[1]))
    elif center:
        rect.center = (int(position[0]), int(position[1]))
    else:
        rect.topleft = (int(position[0]), int(position[1]))

    surface.blit(image, rect)
    return rect


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _scale(c, k):
    return tuple(int(clamp(v * k, 0, 255)) for v in c[:3])


def _side_tint(u):
    """Blue (G.ONE side) -> violet (centre) -> red (RA.ONE side)."""
    u = clamp(u, 0.0, 1.0)
    blue, violet, red = (45, 150, 255), (130, 80, 220), (255, 55, 80)
    return _mix(blue, violet, u * 2) if u < 0.5 else _mix(violet, red, (u - 0.5) * 2)


def make_glow(w, h, color, strength=1.0, steps=20):
    """Soft radial glow on black (opaque RGB) for BLEND_RGB_ADD."""
    w, h = max(2, int(w)), max(2, int(h))
    steps = max(4, min(steps, min(w, h) // 2))
    surf = pygame.Surface((w, h))
    surf.fill((0, 0, 0))
    for i in range(steps):
        f = 1 - i / steps
        k = ((i + 1) / steps) ** 2.0 * strength
        pygame.draw.ellipse(surf, _scale(color, k), (w / 2 - w / 2 * f, h / 2 - h / 2 * f, w * f, h * f))
    if min(w, h) >= 30:
        small = pygame.transform.smoothscale(surf, (max(1, w // 5), max(1, h // 5)))
        surf = pygame.transform.smoothscale(small, (w, h))
    return surf


def draw_gradient(surface, top, bottom):
    width, height = surface.get_size()

    strip = pygame.Surface((1, height))

    for y in range(height):
        t = y / max(1, height - 1)

        color = tuple(
            int(top[i] * (1 - t) + bottom[i] * t)
            for i in range(3)
        )

        strip.set_at((0, y), color)

    surface.blit(
        pygame.transform.scale(strip, (width, height)),
        (0, 0),
    )


def load_character(path, tint, size):
    if path is not None:
        try:
            image = pygame.image.load(str(path)).convert_alpha()
            bounds = image.get_bounding_rect()
            if bounds.width and bounds.height:
                image = image.subsurface(bounds).copy()
            target_w, target_h = size
            src_w, src_h = image.get_size()
            scale = min(target_w / src_w, target_h / src_h)
            fitted = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
            return pygame.transform.smoothscale(image, fitted)

        except (pygame.error, OSError):
            pass

    # Procedural fallback if the image cannot be found.
    width, height = size

    image = pygame.Surface(size, pygame.SRCALPHA)

    pygame.draw.ellipse(
        image,
        (*tint, 65),
        (
            width * 0.15,
            height * 0.04,
            width * 0.7,
            height * 0.92,
        ),
    )

    pygame.draw.circle(
        image,
        tint,
        (width // 2, int(height * 0.2)),
        max(8, width // 6),
    )

    pygame.draw.polygon(
        image,
        tint,
        [
            (width * 0.30, height * 0.35),
            (width * 0.70, height * 0.35),
            (width * 0.88, height * 0.86),
            (width * 0.12, height * 0.86),
        ],
    )

    return image


# ============================================================
# FINAL PROTOCOL BOSS FIGHT
# ============================================================

class ParticleSystem:
    """Ash, embers and lightning sparks using small fixed pools."""

    def __init__(self, w, h, rng, ash=36, embers=18, max_sparks=40):
        self.w, self.h, self.rng, self.max_sparks = w, h, rng, max_sparks
        self.t = 0.0
        self.ash = [
            [rng.uniform(0, w), rng.uniform(0, h), rng.uniform(10, 26),
             rng.choice((1, 1, 2)), rng.uniform(0.35, 0.85), rng.uniform(0, 6.28)]
            for _ in range(ash)
        ]
        self.embers = [
            [rng.uniform(0, w), rng.uniform(h * 0.3, h), rng.uniform(28, 75), rng.uniform(0, 6.28)]
            for _ in range(embers)
        ]
        self.sparks = []

    def spawn_sparks(self, x, y, n=8):
        for _ in range(min(n, self.max_sparks - len(self.sparks))):
            ang = self.rng.uniform(math.pi * 1.05, math.pi * 1.95)
            spd = self.rng.uniform(120, 320)
            life = self.rng.uniform(0.35, 0.7)
            self.sparks.append([x, y, math.cos(ang) * spd, math.sin(ang) * spd, life, life])

    def update(self, dt):
        self.t += dt
        t, w, h = self.t, self.w, self.h
        for p in self.ash:
            p[0] += (-14 + 9 * math.sin(t * 0.4 + p[5])) * dt
            p[1] += p[2] * dt
            if p[1] > h or p[0] < -4:
                p[0], p[1] = self.rng.uniform(0, w * 1.1), -4
        for e in self.embers:
            e[1] -= e[2] * dt
            e[0] += (10 * math.sin(t * 1.3 + e[3]) - 6) * dt
            if e[1] < h * 0.22 or e[0] < -4:
                e[0], e[1] = self.rng.uniform(0, w), self.rng.uniform(h * 0.62, h * 0.95)
        if self.sparks:
            for sp in self.sparks:
                sp[0] += sp[2] * dt
                sp[1] += sp[3] * dt
                sp[3] += 520 * dt
                sp[4] -= dt
            self.sparks = [sp for sp in self.sparks if sp[4] > 0]

    def draw(self, screen):
        t = self.t
        for x, y, _v, size, shade, _ph in self.ash:
            screen.fill((int(185 * shade), int(130 * shade), int(130 * shade)), (int(x), int(y), size, size))
        for x, y, _v, ph in self.embers:
            f = 0.55 + 0.45 * math.sin(t * 9 + ph)
            screen.fill((int(255 * f), int(120 * f), int(40 * f)), (int(x), int(y), 2, 2))
        for x, y, vx, vy, life, mx in self.sparks:
            k = life / mx
            pygame.draw.line(screen, (int(120 + 135 * k), int(185 + 55 * k), 255),
                             (int(x), int(y)), (int(x - vx * 0.025), int(y - vy * 0.025)), 2)


class LightningEffect:
    """Procedural branching blue lightning with smooth flash fade and cached glows."""

    def __init__(self, w, h, rng, particles):
        self.w, self.h, self.rng, self.particles = w, h, rng, particles
        self.ground_y = int(h * 0.74)
        self.timer = rng.uniform(1.0, 2.5)
        self.age, self.life = 99.0, 0.28
        self.power = 0.0
        self.glow = 0.0
        self.vis = 0.0
        self.ground_factor = 0.45
        self.bolt = None

        col = (70, 140, 255)
        hh = int(h * 0.8)
        self.sky_flash = pygame.Surface((w, hh), pygame.SRCALPHA)
        for y in range(hh):
            pygame.draw.line(self.sky_flash, (*col, int(120 * (1 - y / hh) ** 1.2)), (0, y), (w, y))
        sw = int(w * 0.3)
        self.side_left = pygame.Surface((sw, h), pygame.SRCALPHA)
        for x in range(sw):
            pygame.draw.line(self.side_left, (60, 125, 235, int(125 * (1 - x / sw) ** 1.6)), (x, 0), (x, h))
        self.side_right = pygame.transform.flip(self.side_left, True, False)
        self.ground_top = int(h * 0.66)
        fh = h - self.ground_top
        self.ground_flash = pygame.Surface((w, fh), pygame.SRCALPHA)
        for y in range(fh):
            a = 100 * min(1.0, y / max(1, fh * 0.2)) * (1 - 0.35 * y / fh)
            pygame.draw.line(self.ground_flash, (70, 130, 255, int(a)), (0, y), (w, y))
        rw, rh = int(w * 0.55), int(h * 0.14)
        self.road = pygame.Surface((rw, rh), pygame.SRCALPHA)
        for k in range(8):
            f = 1 - k / 8
            pygame.draw.ellipse(self.road, (170, 215, 255, int(8 + 36 * (k / 8))),
                                (rw / 2 - rw / 2 * f, rh / 2 - rh / 2 * f, rw * f, rh * f))

    def _path(self, ax, ay, bx, by, n=11):
        pts = [(ax, ay)]
        for i in range(1, n):
            t = i / n
            pts.append((int(ax + (bx - ax) * t + self.rng.randint(-26, 26)),
                        int(ay + (by - ay) * t + self.rng.randint(-8, 8))))
        pts.append((bx, by))
        return pts

    def strike(self, power=1.0, ground=None):
        rng, w, h = self.rng, self.w, self.h
        if ground is None:
            ground = rng.random() < 0.3
        x0 = rng.randint(int(w * 0.15), int(w * 0.85))
        if ground:
            x1, y1 = rng.randint(int(w * 0.3), int(w * 0.7)), self.ground_y
        else:
            x1, y1 = x0 + rng.randint(-140, 140), rng.randint(int(h * 0.3), int(h * 0.52))
        main = self._path(x0, -6, x1, y1)
        lines = [(main, 2)]
        for i in range(2, len(main) - 1):
            if rng.random() < 0.32:
                d = rng.choice((-1, 1))
                bx, by = main[i]
                br = [(bx, by)]
                for _ in range(rng.randint(3, 6)):
                    bx += d * rng.randint(14, 38)
                    by += rng.randint(10, 30)
                    br.append((bx, by))
                    if rng.random() < 0.2:
                        lines.append(([(bx, by), (bx + d * rng.randint(10, 24), by + rng.randint(8, 22))], 1))
                lines.append((br, 1))
        xs = [p[0] for pts, _ in lines for p in pts]
        ys = [p[1] for pts, _ in lines for p in pts]
        x_min, y_min = min(xs) - 14, min(ys) - 14
        surf = pygame.Surface((max(xs) + 14 - x_min, max(ys) + 14 - y_min), pygame.SRCALPHA)
        for pts, wd in lines:
            q = [(px - x_min, py - y_min) for px, py in pts]
            pygame.draw.lines(surf, (70, 140, 255, 45), False, q, 8 if wd == 2 else 5)
            pygame.draw.lines(surf, (120, 185, 255, 160), False, q, 3 if wd == 2 else 2)
            pygame.draw.lines(surf, (228, 242, 255, 255), False, q, wd)
        self.bolt = (surf, x_min, y_min)
        self.power, self.age = power, 0.0
        self.ground_factor = 1.0 if ground else 0.45
        self.timer = max(self.timer, 1.5)
        self.particles.spawn_sparks(x1, y1, 10 if ground else 4)

    def update(self, dt, low=0.0):
        shake = 0.0
        self.age += dt
        self.timer -= dt
        if self.timer <= 0:
            power = self.rng.uniform(0.55, 1.0)
            self.strike(power)
            self.timer = self.rng.uniform(2.0, 5.5) / (1 + 0.8 * low)
            if power > 0.8:
                shake = 2.0
        flick = 0.0
        if self.age < self.life:
            k = self.age / self.life
            flick = (1 - k) * (0.7 + 0.3 * math.sin(self.age * 55))
        self.glow = max(self.glow * math.exp(-5.0 * dt), flick * self.power)
        self.vis += (self.glow - self.vis) * min(1.0, dt * (28 if self.glow > self.vis else 7))
        return shake

    def draw(self, screen):
        if self.vis < 0.01 and self.age >= self.life:
            return
        v = clamp(self.vis, 0.0, 1.0)
        if self.bolt is not None and self.age < self.life:
            k = self.age / self.life
            surf, bx, by = self.bolt
            surf.set_alpha(int(clamp(255 * (1 - k) * (0.65 + 0.35 * math.sin(self.age * 55 + 1)), 0, 255)))
            screen.blit(surf, (bx, by))
        if v < 0.01:
            return
        self.sky_flash.set_alpha(int(190 * v))
        screen.blit(self.sky_flash, (0, 0))
        self.side_left.set_alpha(int(210 * v))
        self.side_right.set_alpha(int(210 * v))
        screen.blit(self.side_left, (0, 0))
        screen.blit(self.side_right, (self.w - self.side_right.get_width(), 0))
        gv = v * self.ground_factor
        self.ground_flash.set_alpha(int(190 * gv))
        screen.blit(self.ground_flash, (0, self.ground_top))
        self.road.set_alpha(int(230 * gv))
        screen.blit(self.road, (self.w // 2 - self.road.get_width() // 2, int(self.h * 0.86) - self.road.get_height() // 2))


class AtmosphereEffect:
    """Static image base + drifting fog, red sky pulse, particles and lightning."""

    def __init__(self, w, h, rng, path):
        self.w, self.h = w, h
        img = pygame.image.load(str(path)).convert()
        iw, ih = img.get_size()
        scale = max(w / iw, h / ih)
        sw, sh = int(math.ceil(iw * scale)), int(math.ceil(ih * scale))
        img = pygame.transform.smoothscale(img, (sw, sh))
        top = int(clamp(BG_FEET_ANCHOR * sh - 0.72 * h, 0, sh - h))
        self.base = img.subsurface(((sw - w) // 2, top, w, h)).copy()

        self.particles = ParticleSystem(w, h, rng)
        self.lightning = LightningEffect(w, h, rng, self.particles)

        hh = int(h * 0.62)
        self.sky_levels = []
        for lv in range(5):
            surf = pygame.Surface((w, hh))
            for y in range(hh):
                f = (1 - y / hh) ** 1.3 * lv / 4
                pygame.draw.line(surf, (int(36 * f), int(3 * f), int(8 * f)), (0, y), (w, y))
            self.sky_levels.append(surf)

        def fog(seed, hgt, n, col, a_lo, a_hi, rw, rh):
            r = random.Random(seed)
            w2 = w * 2
            layer = pygame.Surface((w2, hgt), pygame.SRCALPHA)
            for _ in range(n):
                cx, cy = r.randint(0, w2), r.randint(int(hgt * 0.2), int(hgt * 0.8))
                ew, eh, a = r.randint(*rw), r.randint(*rh), r.randint(a_lo, a_hi)
                for ox in (0, -w2):
                    pygame.draw.ellipse(layer, (*col, a), (cx + ox - ew // 2, cy - eh // 2, ew, eh))
            small = pygame.transform.smoothscale(layer, (w2 // 6, max(1, hgt // 6)))
            return pygame.transform.smoothscale(small, (w2, hgt))

        self.mist = fog(5, int(h * 0.28), 26, (150, 30, 45), 22, 44, (200, 420), (50, 120))
        self.mist_y = int(h * 0.34)
        self.ground_fog = fog(8, int(h * 0.30), 30, (120, 70, 74), 30, 58, (220, 480), (50, 130))
        self.ground_fog_y = int(h * 0.68)
        self.mist.set_alpha(190)
        self.ground_fog.set_alpha(210)
        self.mist_x = self.fog_x = 0.0
        self.p1 = self.p2 = 0.0

    def update(self, dt, low=0.0):
        self.mist_x = (self.mist_x + 9 * dt) % (self.w * 2)
        self.fog_x = (self.fog_x + 17 * dt) % (self.w * 2)
        self.p1 += 0.55 * dt
        self.p2 += 1.37 * dt
        self.particles.update(dt)
        return self.lightning.update(dt, low)

    def _tile(self, screen, layer, x, y):
        w2 = self.w * 2
        screen.blit(layer, (-int(x), y))
        screen.blit(layer, (w2 - int(x), y))

    def draw(self, screen, low=0.0):
        screen.blit(self.base, (0, 0))
        v = 0.35 + 0.2 * math.sin(self.p1) + 0.12 * math.sin(self.p2) + 0.4 * low
        idx = int(round(clamp(v, 0.0, 1.0) * 4))
        if idx:
            screen.blit(self.sky_levels[idx], (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        self._tile(screen, self.mist, self.mist_x, self.mist_y)
        self.lightning.draw(screen)
        self._tile(screen, self.ground_fog, self.fog_x, self.ground_fog_y)
        self.particles.draw(screen)


class RooftopArena:
    """Dark cyberpunk rooftop. The scene is pre-rendered once; only lights, beams,
    fog and particles are animated per frame."""

    def __init__(self, w, h, rng):
        self.w, self.h, self.rng = w, h, rng
        self.t = 0.0
        self.horizon = int(h * 0.56)
        self.floor_y = int(h * BG_FEET_ANCHOR)
        self.particles = ParticleSystem(w, h, rng, ash=24, embers=16)
        self.lightning = LightningEffect(w, h, rng, self.particles)
        self.beacons = []
        self.tower_rings = []
        self._glows = {}
        self._beam_cache = {}
        self.base = self._build_base()

        length = int(h * 0.62)
        bw = int(w * 0.17)
        specs = ((0.08, (60, 150, 255), -6, 0.55, 0.0), (0.31, (70, 220, 255), -3, 0.42, 1.7),
                 (0.69, (255, 80, 120), 3, 0.48, 3.1), (0.92, (255, 60, 80), 6, 0.60, 4.4))
        self.beams = [
            {"x": w * x, "lean": lean, "spd": spd, "ph": ph,
             "surf": self._make_beam(length, bw, 22, col, 120)}
            for x, col, lean, spd, ph in specs
        ]
        self.mist = self._build_fog(5, int(h * 0.28), 26, 24, 46, (200, 420), (50, 120), 0.55)
        self.ground_fog = self._build_fog(8, int(h * 0.30), 30, 30, 58, (220, 480), (50, 130), 0.45)
        self.mist.set_alpha(185)
        self.ground_fog.set_alpha(205)
        self.motes = []
        for _ in range(30):
            x = rng.uniform(0, w)
            self.motes.append([x, rng.uniform(self.horizon * 0.5, h), rng.uniform(14, 40),
                               rng.uniform(0, 6.28), _side_tint(x / w)])

    # ---------- pre-rendering ----------
    def _make_beam(self, length, top_w, bottom_w, color, peak):
        surf = pygame.Surface((top_w, length), pygame.SRCALPHA)
        surf.fill((*color, 0))
        cx = top_w / 2
        for y in range(length):
            t = y / max(1, length - 1)          # 0 at the top, 1 at the lamp
            half = (top_w * (1 - t) + bottom_w * t) / 2
            pygame.draw.line(surf, (*color, int(peak * t ** 1.3)), (int(cx - half), y), (int(cx + half), y))
        small = pygame.transform.smoothscale(surf, (max(1, top_w // 3), max(1, length // 3)))
        return pygame.transform.smoothscale(small, (top_w, length))

    def _build_fog(self, seed, height, count, a_lo, a_hi, rw, rh, tint_mix):
        r = random.Random(seed)
        lw = self.w + 200
        layer = pygame.Surface((lw, height), pygame.SRCALPHA)
        layer.fill((60, 60, 80, 0))
        for _ in range(count):
            cx, cy = r.randint(0, lw), r.randint(int(height * 0.2), int(height * 0.8))
            ew, eh, a = r.randint(*rw), r.randint(*rh), r.randint(a_lo, a_hi)
            col = _mix((70, 70, 92), _side_tint(cx / lw), tint_mix)
            pygame.draw.ellipse(layer, (*col, a), (cx - ew // 2, cy - eh // 2, ew, eh))
        small = pygame.transform.smoothscale(layer, (lw // 6, max(1, height // 6)))
        return pygame.transform.smoothscale(small, (lw, height))

    def _build_base(self):
        w, h, hz = self.w, self.h, self.horizon
        rng = random.Random(21)
        base = pygame.Surface((w, h))
        base.fill((4, 6, 14))
        for y in range(hz + 2):
            pygame.draw.line(base, _mix((3, 5, 14), (34, 12, 44), (y / max(1, hz)) ** 1.5), (0, y), (w, y))
        # coloured sky glow: blue behind G.ONE, red behind RA.ONE, violet around the tower
        for gx, col, st, gw in ((0.16, (30, 110, 255), 0.85, 0.9), (0.84, (255, 36, 60), 0.85, 0.9),
                                (0.5, (120, 50, 210), 0.5, 0.6)):
            g = make_glow(int(w * gw), int(h * 0.95), col, st)
            base.blit(g, (int(w * gx - g.get_width() / 2), int(hz - g.get_height() * 0.60)),
                      special_flags=pygame.BLEND_RGB_ADD)
        for _ in range(70):
            sx, sy = rng.randrange(w), rng.randrange(0, int(hz * 0.8))
            base.set_at((sx, sy), _scale(_side_tint(sx / w), 0.55))

        # two skyline layers with lit windows, side-tinted edges and blinking antennas
        layers = ((60, 230, 38, 84, (10, 14, 30), 0.55, True), (40, 170, 56, 120, (6, 8, 20), 0.95, False))
        for hmin, hmax, wmin, wmax, body, bright, antennas in layers:
            x = -20
            while x < w:
                bw, bh = rng.randint(wmin, wmax), rng.randint(hmin, hmax)
                top = hz - bh
                tint = _side_tint((x + bw / 2) / w)
                rect = pygame.Rect(x, top, bw, bh)
                pygame.draw.rect(base, body, rect)
                pygame.draw.line(base, _scale(tint, 0.65 * bright + 0.2), rect.topleft, rect.topright, 2)
                pygame.draw.line(base, _scale(tint, 0.35), rect.topleft, rect.bottomleft, 1)
                for wy in range(top + 10, hz - 6, 13):
                    for wx in range(x + 6, x + bw - 6, 10):
                        if rng.random() < 0.20:
                            c = _mix(tint, (255, 255, 255), rng.random() * 0.4)
                            base.fill(_scale(c, 0.5 * bright + 0.15), (wx, wy, 3, 5))
                if antennas and rng.random() < 0.35:
                    ax, alen = x + bw // 2, rng.randint(18, 44)
                    pygame.draw.line(base, _scale(tint, 0.6), (ax, top), (ax, top - alen), 1)
                    self.beacons.append((ax, top - alen, tint, rng.uniform(0, 6.28)))
                x += bw + rng.randint(-4, 8)

        # central tower: blue-lit left face, red-lit right face
        cx, top_y = w // 2, int(h * 0.05)
        pygame.draw.polygon(base, (7, 10, 24), [(cx - 120, hz + 6), (cx - 84, hz - 84), (cx + 84, hz - 84), (cx + 120, hz + 6)])
        pygame.draw.line(base, (60, 150, 255), (cx - 120, hz + 6), (cx - 84, hz - 84), 2)
        pygame.draw.line(base, (255, 60, 90), (cx + 120, hz + 6), (cx + 84, hz - 84), 2)
        body = [(cx - 40, hz - 70), (cx - 24, int(h * 0.30)), (cx - 10, int(h * 0.15)), (cx - 3, top_y + 30),
                (cx + 3, top_y + 30), (cx + 10, int(h * 0.15)), (cx + 24, int(h * 0.30)), (cx + 40, hz - 70)]
        pygame.draw.polygon(base, (9, 12, 28), body)
        pygame.draw.lines(base, (60, 150, 255), False, body[:4], 2)
        pygame.draw.lines(base, (255, 60, 90), False, body[4:], 2)
        pygame.draw.line(base, (140, 100, 240), (cx, top_y + 30), (cx, hz - 80), 1)
        pygame.draw.line(base, (140, 100, 240), (cx, top_y), (cx, top_y + 30), 2)
        for wy in range(hz - 74, hz - 6, 12):
            for k in range(-4, 5):
                if k and rng.random() < 0.55:
                    base.fill(_scale(_side_tint(0.5 + k * 0.1), 0.6), (cx + k * 18 - 2, wy, 4, 5))
        for ry, rw in ((int(h * 0.20), 16), (int(h * 0.27), 22), (int(h * 0.34), 28), (int(h * 0.41), 34), (hz - 52, 52)):
            pygame.draw.ellipse(base, (110, 80, 210), (cx - rw, ry - 5, rw * 2, 10), 2)
            self.tower_rings.append((ry, rw))

        # rooftop floor with perspective grid
        for x in range(0, w, 4):
            pygame.draw.rect(base, _scale(_side_tint((x + 2) / w), 0.10), (x, hz, 4, h - hz))
        shade = pygame.Surface((w, h - hz), pygame.SRCALPHA)
        for y in range(h - hz):
            pygame.draw.line(shade, (2, 3, 8, int(150 * (y / (h - hz)) ** 1.2)), (0, y), (w, y))
        base.blit(shade, (0, hz))
        for xb in range(-1600, w + 1601, 140):
            pygame.draw.line(base, _scale(_side_tint(xb / w), 0.22), (cx, hz), (xb, h), 1)
        n = 14
        for i in range(1, n + 1):
            y = hz + int((h - hz) * (i / n) ** 2.0)
            for x0 in range(0, w, 40):
                pygame.draw.line(base, _scale(_side_tint((x0 + 20) / w), 0.18 + 0.4 * (i / n)), (x0, y), (x0 + 40, y), 1)
        # parapet with neon strip
        base.fill((5, 7, 15), (0, hz - 8, w, 10))
        for x0 in range(0, w, 20):
            pygame.draw.line(base, _side_tint((x0 + 10) / w), (x0, hz - 8), (x0 + 20, hz - 8), 2)

        # coloured light pools on the roof and back-lighting behind each fighter
        fy = self.floor_y
        for fx, col in ((0.25, (50, 140, 255)), (0.73, (255, 50, 70))):
            g = make_glow(int(w * 0.42), int(h * 0.12), col, 0.75)
            base.blit(g, (int(w * fx - g.get_width() / 2), fy - g.get_height() // 2 + 6), special_flags=pygame.BLEND_RGB_ADD)
            g2 = make_glow(int(w * 0.34), int(h * 0.62), col, 0.40)
            base.blit(g2, (int(w * fx - g2.get_width() / 2), fy - int(g2.get_height() * 0.75)), special_flags=pygame.BLEND_RGB_ADD)

        # vignette baked into the static layer
        v = pygame.Surface((w, h))
        v.fill((84, 84, 90))
        steps = 24
        for i in range(steps):
            f = 1.55 - 0.75 * i / (steps - 1)
            c = int(84 + 171 * (i / (steps - 1)) ** 0.8)
            pygame.draw.ellipse(v, (c, c, c), (w / 2 - w * f / 2, h / 2 - h * f / 2, w * f, h * f))
        small = pygame.transform.smoothscale(v, (max(1, w // 6), max(1, h // 6)))
        base.blit(pygame.transform.smoothscale(small, (w, h)), (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        return base

    # ---------- per-frame ----------
    def add_glow(self, screen, cx, cy, gw, gh, color, level):
        lv = max(0, min(6, int(round(level * 6))))
        if lv == 0:
            return
        gw, gh = int(gw), int(gh)
        key = (gw, gh, tuple(color), lv)
        g = self._glows.get(key)
        if g is None:
            g = self._glows[key] = make_glow(gw, gh, color, lv / 6.0)
        screen.blit(g, (int(cx - gw / 2), int(cy - gh / 2)), special_flags=pygame.BLEND_RGB_ADD)

    def update(self, dt, low=0.0):
        self.t += dt
        for m in self.motes:
            m[1] -= m[2] * dt
            m[0] += math.sin(self.t * 0.9 + m[3]) * 8 * dt
            if m[1] < self.horizon * 0.5:
                m[0], m[1] = self.rng.uniform(0, self.w), self.rng.uniform(self.horizon, self.h)
        self.particles.update(dt)
        return self.lightning.update(dt, low)

    def draw(self, screen, low=0.0):
        t, w, h, hz = self.t, self.w, self.h, self.horizon
        cx = w // 2
        screen.blit(self.base, (0, 0))

        # swaying searchlight beams
        for b in self.beams:
            ang = int(round(b["lean"] + 9 * math.sin(t * b["spd"] + b["ph"])))
            key = (id(b), ang)
            rot = self._beam_cache.get(key)
            if rot is None:
                rot = self._beam_cache[key] = pygame.transform.rotate(b["surf"], ang)
            half, rad = b["surf"].get_height() / 2, math.radians(ang)
            rot.set_alpha(int(150 + 60 * math.sin(t * 1.7 + b["ph"])))
            screen.blit(rot, rot.get_rect(center=(int(b["x"] - half * math.sin(rad)), int(hz + 4 - half * math.cos(rad)))))

        screen.blit(self.mist, (-100 + int(36 * math.sin(t * 0.13)), int(h * 0.40)))
        self.lightning.draw(screen)

        # tower rings, beacon, skyline antennas, red storm glow as RA.ONE weakens
        pulse = 0.5 + 0.5 * math.sin(t * 2.2)
        for i, (ry, rw) in enumerate(self.tower_rings):
            self.add_glow(screen, cx, ry, rw * 4 + 20, 44, (150, 100, 255), 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 3.0 - i * 0.9)))
        self.add_glow(screen, cx, int(h * 0.05), 90, 90,
                      (255, 70, 90) if int(t * 1.2) % 2 == 0 else (70, 160, 255), 0.5 + 0.5 * pulse)
        for bx, by, tint, ph in self.beacons:
            self.add_glow(screen, bx, by, 26, 26, tint, 1.0 if math.sin(t * 2.3 + ph) > 0.2 else 0.2)
        self.add_glow(screen, int(w * 0.78), int(h * 0.20), int(w * 0.7), int(h * 0.55), (255, 30, 50),
                      0.12 + 0.55 * low * (0.8 + 0.2 * math.sin(t * 7)))

        # travelling floor pulse + breathing ground glow under each fighter
        q = (t * 0.30) % 1.0
        y = hz + int((h - hz) * q ** 2.0)
        for x0 in range(0, w, 40):
            pygame.draw.line(screen, _scale(_side_tint((x0 + 20) / w), 0.85 * (1.0 - q * 0.5)), (x0, y), (x0 + 40, y), 2)
        self.add_glow(screen, w * 0.25, self.floor_y + 8, w * 0.34, h * 0.09, (50, 140, 255), 0.35 + 0.35 * pulse)
        self.add_glow(screen, w * 0.73, self.floor_y + 8, w * 0.34, h * 0.09, (255, 50, 70), 0.35 + 0.35 * (1 - pulse))

        screen.blit(self.ground_fog, (-100 + int(60 * math.sin(t * 0.09 + 1.0)), int(h * 0.66)))
        for m in self.motes:
            self.add_glow(screen, m[0], m[1], 12, 12, m[4], 0.35 + 0.35 * math.sin(t * 2 + m[3]))
        self.particles.draw(screen)


class FinalProtocolBonus:

    def __init__(self, screen, clock, ppo_model=None, camera_input_provider=None):

        self.screen = screen
        self.clock = clock
        self.camera_input_provider = camera_input_provider
        self.camera_controls_live = False
        self.hand_ui_state = {}
        self._hand_ui_frame_id = -2
        self._left_hand_surface = None
        self._right_hand_surface = None
        self._hand_panel_font_cache = {}
        self.gesture_move_x = 0.5
        self.last_camera_gesture = None
        self.gesture_dash_latched = False
        self.player_heal_cooldown = 0.0
        self.gesture_status = "CAMERA WAITING"

        self.width, self.height = screen.get_size()

        self.rng = random.Random()

        self.elapsed = 0.0
        self.shake = 0.0
        self.flash = 0.0

        self.atmosphere = None
        self._atmo_failed = False

        self.particles = []
        self.rings = []

        self.stars = [
            (
                self.rng.randrange(self.width),
                self.rng.randrange(self.height),
                self.rng.choice((1, 1, 1, 2)),
                self.rng.uniform(8, 32),
            )
            for _ in range(100)
        ]

        # Large realistic sprites with identical height so both fighters read at the same scale.
        fit_h = int(self.height * 0.62)
        fit = (int(fit_h * 0.9), fit_h)
        self.gone_img = load_character(GONE_PATH, BLUE, fit)
        self.raone_img = load_character(RAONE_PATH, RED, fit)
        th = min(self.gone_img.get_height(), self.raone_img.get_height())
        for attr in ("gone_img", "raone_img"):
            img = getattr(self, attr)
            if img.get_height() != th:
                sw = max(1, int(round(img.get_width() * th / img.get_height())))
                setattr(self, attr, pygame.transform.smoothscale(img, (sw, th)))
        self.sprite_h = th
        self.player_character_name = "G.ONE"
        self._player_sprite_size = fit
        self.k = th / 435.0
        self.chest_h = int(th * 0.56)
        self.hand_h = int(th * 0.52)

        # Health and energy
        self.gone_hp = 100.0
        self.raone_hp = 100.0
        self.energy = 100.0

        # Player movement
        self.player_x = self.width * 0.25
        self.player_y = self.height * 0.72

        self.player_vy = 0.0
        self.player_facing = 1

        self.player_invuln = 0.0
        self.dash_time = 0.0

        self.blocking = False

        # Combat timers
        self.attack_time = 0.0
        self.attack_cooldown = 0.0

        self.special_time = 0.0
        self.special_cooldown = 0.0

        # Boss movement and attacks
        self.boss_x = self.width * 0.73
        self.boss_y = self.height * 0.72

        self.boss_attack_timer = 1.5
        self.boss_attack = None
        self.boss_attack_time = 0.0

        self.boss_stun = 0.0
        self.boss_phase = 1

        self.music_started = False

        # ---- Combat / animation state (mirrors Rounds 1-3 feel) ----
        self.ppo_model = ppo_model if ppo_model is not None else self._load_ppo()
        self.ppo_acc = 0.0
        self.b_atk_cd = self.b_shield_cd = self.b_heal_cd = self.b_spec_cd = 0.0
        # PPO was trained with a 10-value observation including G.ONE's
        # next-attack timer. Keep the same normalized feature semantics here.
        self.g_attack_timer = self.rng.uniform(2.4, 3.5)
        self.boss_shield = 0.0
        self.boss_bolt_delay = 0.0
        self.boss_cast_vis = 0.0
        self.boss_hit = 0.0
        self.boss_kb = 0.0
        self.ai_label = ""
        self.ai_label_until = 0.0
        self.last_player_action = -99.0
        self.fight_time = 0.0
        self.outcome = None          # "victory" / "defeat": set by the first HP to reach 0
        self.exit_requested = False
        self.combo = 0
        self.combo_timer = 0.0
        self.combo_pulse = 0.0
        self.boss_ghost_timer = 0.0
        self.hp_trail = {"g": 100.0, "r": 100.0}
        self._hud = None
        self._pose_cache = {}
        self._glow_cache = {}
        self._scratch = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self._flash_surf = pygame.Surface((self.width, self.height))
        self._flash_surf.fill((255, 255, 255))
        self._shake_buf = pygame.Surface((self.width, self.height))

        self.projectiles = []
        self.afterimages = []
        self.floaters = []
        self.impacts = []
        self.hitstop = 0.0

        self.attack_pending = False
        self.charge_time = 0.0
        self.player_hit = 0.0
        self.player_kb = 0.0
        self.shield_vis = 0.0
        self.shield_hit = 0.0
        self.shield_hit_pos = (0, 0)
        self.land_squash = 0.0
        self.was_airborne = False
        self.dash_dir = 1
        self.ghost_timer = 0.0

        self.fx_layer = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self.gone_ghost = self.gone_img.copy()
        self.gone_ghost.fill((10, 110, 255, 0), special_flags=pygame.BLEND_RGB_ADD)
        self.raone_flash = self.raone_img.copy()
        self.raone_flash.fill((170, 170, 170, 0), special_flags=pygame.BLEND_RGB_ADD)
        self.gone_flash = self.gone_img.copy()
        self.gone_flash.fill((230, 40, 40, 0), special_flags=pygame.BLEND_RGB_ADD)
        self.gone_ghost_flip = pygame.transform.flip(self.gone_ghost, True, False)
        self.raone_ghost = self.raone_img.copy()
        self.raone_ghost.fill((150, 10, 30, 0), special_flags=pygame.BLEND_RGB_ADD)
        self.raone_ghost_flip = pygame.transform.flip(self.raone_ghost, True, False)
        self.sfx = self._build_sfx()

        self.start_music()

    # ========================================================
    # BONUS ROUND CHARACTER SELECT
    # ========================================================

    def select_player_character(self):
        """Let the player choose one of the four visual skins before the boss fight.

        The selected sprite replaces only G.ONE's appearance. Health, damage,
        movement, cooldowns, gesture controls and PPO opponent remain unchanged.
        """
        options = []
        preview_size = self._player_sprite_size
        for name, candidates in PLAYER_ROSTER:
            path = find_asset(*candidates)
            sprite = load_character(path, BLUE, preview_size)
            options.append((name, sprite))

        selected = 0
        title_font = get_font(48, True)
        name_font = get_font(22, True)
        hint_font = get_font(17, False)
        card_w = int(self.width * 0.205)
        card_h = int(self.height * 0.59)
        gap = int(self.width * 0.025)
        total_w = card_w * len(options) + gap * (len(options) - 1)
        start_x = (self.width - total_w) // 2
        card_y = int(self.height * 0.25)
        clock = pygame.time.Clock()

        while True:
            dt = min(clock.tick(60) / 1000.0, 0.05)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.exit_requested = True
                    return False
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.exit_requested = True
                        return False
                    if event.key in (pygame.K_LEFT, pygame.K_a):
                        selected = (selected - 1) % len(options)
                    elif event.key in (pygame.K_RIGHT, pygame.K_d):
                        selected = (selected + 1) % len(options)
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                        self.player_character_name, self.gone_img = options[selected]
                        # Rebuild appearance-derived effects for the chosen sprite.
                        self.gone_ghost = self.gone_img.copy()
                        self.gone_ghost.fill((10, 110, 255, 0), special_flags=pygame.BLEND_RGB_ADD)
                        self.gone_ghost_flip = pygame.transform.flip(self.gone_ghost, True, False)
                        self.gone_flash = self.gone_img.copy()
                        self.gone_flash.fill((230, 40, 40, 0), special_flags=pygame.BLEND_RGB_ADD)
                        self._pose_cache.clear()
                        self._glow_cache.clear()
                        self._hud = None
                        return True

            self.draw_background()
            overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            overlay.fill((2, 5, 18, 190))
            self.screen.blit(overlay, (0, 0))
            draw_text(self.screen, "CHOOSE YOUR FIGHTER", 42, WHITE,
                      (self.width // 2, int(self.height * 0.105)), bold=True)
            draw_text(self.screen, "Same abilities  •  Same combat stats  •  Your own look",
                      17, MUTED, (self.width // 2, int(self.height * 0.165)))

            for i, (name, sprite) in enumerate(options):
                x = start_x + i * (card_w + gap)
                rect = pygame.Rect(x, card_y, card_w, card_h)
                active = i == selected
                fill = (13, 28, 52, 235) if active else (7, 13, 29, 220)
                card = pygame.Surface(rect.size, pygame.SRCALPHA)
                card.fill(fill)
                self.screen.blit(card, rect.topleft)
                edge = BLUE if active else (65, 83, 112)
                pygame.draw.rect(self.screen, edge, rect, 3 if active else 1, border_radius=14)
                if active:
                    pygame.draw.rect(self.screen, (90, 190, 255), rect.inflate(8, 8), 1, border_radius=17)
                image = sprite
                max_preview = (int(card_w * 0.88), int(card_h * 0.76))
                scale = min(max_preview[0] / image.get_width(), max_preview[1] / image.get_height(), 1.0)
                if scale < 1.0:
                    image = pygame.transform.smoothscale(
                        image, (max(1, int(image.get_width() * scale)),
                                max(1, int(image.get_height() * scale)))
                    )
                image_rect = image.get_rect(center=(rect.centerx, rect.y + int(card_h * 0.46)))
                self.screen.blit(image, image_rect)
                draw_text(self.screen, name, 19, WHITE if active else MUTED,
                          (rect.centerx, rect.bottom - 35), bold=True)

            draw_text(self.screen, "A / D or  ← / →   SELECT", 17, BLUE_WHITE,
                      (self.width // 2, int(self.height * 0.91)), bold=True)
            draw_text(self.screen, "ENTER   CONFIRM", 15, GOLD,
                      (self.width // 2, int(self.height * 0.95)), bold=True)
            pygame.display.flip()

    # ========================================================
    # PPO / SFX HELPERS
    # ========================================================

    def _load_ppo(self):
        """Load the same PPO checkpoint game.py uses (only if not handed in)."""
        try:
            from stable_baselines3 import PPO
            candidates = (
                Path.home() / "Downloads" / "raone_ppo_starter" / "models" / "best" / "best_model.zip",
                Path.home() / "Downloads" / "raone_ppo_starter" / "models" / "aggressive" / "best" / "best_model.zip",
                Path("raone_ppo_starter") / "models" / "best" / "best_model.zip",
                Path("raone_ppo_starter") / "models" / "aggressive" / "best" / "best_model.zip",
                Path("raone_ppo_starter 2") / "models" / "aggressive" / "best" / "best_model.zip",
                Path("models") / "raone_ppo_best.zip",
                Path("models") / "raone_ppo_final.zip",
            )
            for base in (Path.cwd(), ROOT, ROOT.parent):
                for rel in candidates:
                    model_path = base / rel
                    if model_path.is_file():
                        print(f"Final Protocol: loading RA.ONE PPO from {model_path}")
                        return PPO.load(str(model_path), device="cpu")
            print("Final Protocol: PPO checkpoint not found; using scripted RA.ONE")
        except Exception as exc:
            print(f"Final Protocol: PPO unavailable, using scripted RA.ONE: {exc}")
        return None

    def _build_sfx(self):
        """Tiny synthesized effects; silently empty if the mixer is unusable."""
        sfx = {}
        try:
            import numpy as np
            info = pygame.mixer.get_init()
            if not info or abs(info[1]) != 16:
                return sfx
            rate, _, ch = info

            def tone(f0, f1, dur, vol=0.35, noise=0.0):
                n = int(rate * dur)
                t = np.linspace(0, 1, n, endpoint=False)
                phase = 2 * np.pi * np.cumsum(f0 + (f1 - f0) * t) / rate
                w = np.sin(phase) * (1 - noise) + np.random.uniform(-1, 1, n) * noise
                a = (w * (1 - t) ** 1.5 * vol * 32767).astype(np.int16)
                if ch == 2:
                    a = np.column_stack((a, a))
                return pygame.sndarray.make_sound(np.ascontiguousarray(a))

            sfx["swing"] = tone(900, 300, 0.12, 0.3, 0.3)
            sfx["hit"] = tone(220, 60, 0.18, 0.45, 0.5)
            sfx["charge"] = tone(200, 900, 0.40, 0.25)
            sfx["blast"] = tone(700, 120, 0.35, 0.4, 0.2)
            sfx["boom"] = tone(120, 35, 0.55, 0.5, 0.6)
            sfx["shield"] = tone(620, 480, 0.14, 0.3)
            sfx["dash"] = tone(500, 1500, 0.14, 0.25, 0.4)
            sfx["land"] = tone(110, 50, 0.10, 0.35, 0.5)
            sfx["bolt"] = tone(500, 250, 0.16, 0.3, 0.2)
        except Exception:
            return {}
        return sfx

    def play(self, name):
        snd = self.sfx.get(name)
        if snd:
            try:
                snd.play()
            except pygame.error:
                pass

    def floater(self, x, y, text, color, size=26):
        self.floaters.append([x, y, str(text), color, 0.8, size])

    def add_impact(self, x, y, color, radius=110, life=0.28):
        self.impacts.append({"x": x, "y": y, "r": radius, "life": life, "max": life, "color": color})

    # ========================================================
    # AUDIO
    # ========================================================

    def start_music(self):

        try:
            if MUSIC_PATH and pygame.mixer.get_init():

                pygame.mixer.music.load(str(MUSIC_PATH))

                pygame.mixer.music.set_volume(0.48)

                pygame.mixer.music.play(
                    -1,
                    fade_ms=900,
                )

                self.music_started = True

        except pygame.error:
            self.music_started = False

    def stop_music(self):

        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.fadeout(700)

        except pygame.error:
            pass

    # ========================================================
    # PARTICLE EFFECTS
    # ========================================================

    def particle_burst(
        self,
        x,
        y,
        color,
        count=18,
        power=240,
    ):

        for _ in range(count):

            angle = self.rng.uniform(0, math.tau)

            speed = self.rng.uniform(
                power * 0.25,
                power,
            )

            self.particles.append(
                {
                    "x": x,
                    "y": y,
                    "vx": math.cos(angle) * speed,
                    "vy": math.sin(angle) * speed,
                    "life": self.rng.uniform(0.25, 0.8),
                    "max": 0.8,
                    "size": self.rng.randint(2, 5),
                    "color": color,
                }
            )

    def create_ring(
        self,
        x,
        y,
        color,
        max_radius=150,
    ):

        self.rings.append(
            {
                "x": x,
                "y": y,
                "r": 8,
                "max": max_radius,
                "life": 0.45,
                "color": color,
            }
        )

    def update_effects(self, dt):

        for particle in self.particles[:]:

            particle["x"] += particle["vx"] * dt
            particle["y"] += particle["vy"] * dt

            particle["vy"] += 110 * dt

            particle["life"] -= dt

            particle["vx"] *= max(
                0,
                1 - 1.7 * dt,
            )

            if particle["life"] <= 0:
                self.particles.remove(particle)

        for ring in self.rings[:]:

            ring["life"] -= dt

            ring["r"] += (
                ring["max"] / 0.45
            ) * dt

            if ring["life"] <= 0:
                self.rings.remove(ring)

        self.shake = max(
            0,
            self.shake - 28 * dt,
        )

        self.flash = max(
            0,
            self.flash - 2.7 * dt,
        )

    # ========================================================
    # BACKGROUND
    # ========================================================

    def draw_background(self):

        if self.atmosphere is None and not self._atmo_failed:
            try:
                if USE_IMAGE_BACKGROUND and BG_IMAGE_PATH is not None:
                    self.atmosphere = AtmosphereEffect(self.width, self.height, self.rng, BG_IMAGE_PATH)
                else:
                    self.atmosphere = RooftopArena(self.width, self.height, self.rng)
            except Exception as exc:
                print(f"Final Protocol: arena build failed, using fallback background: {exc}")
                self._atmo_failed = True
        if self.atmosphere is None:
            return self._draw_procedural_background()

        dt = clamp(self.clock.get_time() / 1000.0, 0.0, 0.05) or (1 / 60)
        low = 1.0 - clamp(self.raone_hp / 100.0, 0.0, 1.0)
        shake = self.atmosphere.update(dt, low)
        if shake:
            self.shake = max(self.shake, shake)
        self.atmosphere.draw(self.screen, low)

    def _draw_procedural_background(self):

        draw_gradient(
            self.screen,
            (13, 18, 36),
            (2, 4, 10),
        )

        horizon = int(self.height * 0.70)

        # Futuristic skyline
        for i in range(20):

            building_width = self.width // 17
            x = i * (self.width // 18) - 10

            building_height = 70 + ((i * 47) % 170)

            color = (
                9 + i % 3,
                17 + i % 7,
                31 + i % 9,
            )

            pygame.draw.rect(
                self.screen,
                color,
                (
                    x,
                    horizon - building_height,
                    building_width,
                    building_height,
                ),
            )

            for wy in range(
                horizon - building_height + 12,
                horizon - 8,
                20,
            ):

                for wx in range(
                    x + 8,
                    x + building_width - 5,
                    17,
                ):

                    if (wx + wy + i) % 4:

                        pygame.draw.rect(
                            self.screen,
                            (18, 46, 68),
                            (wx, wy, 4, 6),
                        )

        # Red energy orb
        orb = pygame.Surface(
            (260, 260),
            pygame.SRCALPHA,
        )

        pygame.draw.circle(
            orb,
            (255, 28, 56, 20),
            (130, 130),
            120,
        )

        pygame.draw.circle(
            orb,
            (255, 28, 56, 35),
            (130, 130),
            88,
            3,
        )

        pygame.draw.circle(
            orb,
            (255, 49, 73, 70),
            (130, 130),
            61,
            2,
        )

        self.screen.blit(
            orb,
            (
                int(self.width * 0.63 - 130),
                int(self.height * 0.13 - 130),
            ),
        )

        # Arena floor
        pygame.draw.polygon(
            self.screen,
            (6, 13, 24),
            [
                (0, horizon),
                (self.width, horizon),
                (self.width, self.height),
                (0, self.height),
            ],
        )

        for i in range(1, 13):

            y = horizon + (
                (i / 12) ** 1.8
                * (self.height - horizon)
            )

            pygame.draw.line(
                self.screen,
                (20, 46, 68),
                (0, int(y)),
                (self.width, int(y)),
                1,
            )

        for x in range(
            -self.width,
            self.width * 2,
            100,
        ):

            pygame.draw.line(
                self.screen,
                (15, 39, 60),
                (self.width // 2, horizon),
                (x, self.height),
                1,
            )

        # Floating stars
        for x, y, radius, speed in self.stars:

            yy = (y + self.elapsed * speed) % self.height

            pygame.draw.circle(
                self.screen,
                (30, 53, 76),
                (x, int(yy)),
                radius,
            )

        # Reactor core
        cx = int(self.width * 0.51)
        cy = int(self.height * 0.52)

        pulse = 1 + 0.08 * math.sin(self.elapsed * 3.5)

        pygame.draw.circle(
            self.screen,
            (16, 40, 65),
            (cx, cy),
            int(120 * pulse),
            2,
        )

        pygame.draw.circle(
            self.screen,
            (20, 65, 93),
            (cx, cy),
            int(84 * pulse),
            2,
        )

        pygame.draw.circle(
            self.screen,
            (28, 115, 153),
            (cx, cy),
            48,
            1,
        )

        for angle in range(0, 360, 45):

            radians = math.radians(
                angle + self.elapsed * 22
            )

            x1 = cx + math.cos(radians) * 95
            y1 = cy + math.sin(radians) * 95

            x2 = cx + math.cos(radians) * 116
            y2 = cy + math.sin(radians) * 116

            pygame.draw.line(
                self.screen,
                (32, 99, 139),
                (int(x1), int(y1)),
                (int(x2), int(y2)),
                3,
            )

        # Dynamic branching lightning and storm glow. The red storm
        # intensifies as RA.ONE loses health, signalling overdrive.
        boss_ratio = clamp(self.raone_hp / 100.0, 0.0, 1.0)
        storm = 1.0 + (1.0 - boss_ratio) * 2.4
        if self.rng.random() < 0.16 * storm:
            sx = self.rng.randrange(self.width)
            sy = self.rng.randrange(max(1, int(self.height * 0.42)))
            points = [(sx, sy)]
            for _ in range(self.rng.randint(4, 8)):
                sx += self.rng.randint(-65, 65)
                sy += self.rng.randint(12, 48)
                points.append((int(clamp(sx, 0, self.width)), int(clamp(sy, 0, self.height * 0.76))))
            lightning = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            pygame.draw.lines(lightning, (255, 35, 65, 45), False, points, 9)
            pygame.draw.lines(lightning, (255, 35, 65, 150), False, points, 3)
            pygame.draw.lines(lightning, (255, 205, 220, 230), False, points, 1)
            self.screen.blit(lightning, (0, 0))
        # Ground-hugging smoke wisps
        smoke = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        for i in range(7):
            x = int((i * 181 + self.elapsed * (18 + i * 2)) % (self.width + 180) - 90)
            y = int(self.height * 0.77 + math.sin(self.elapsed * 1.3 + i) * 20)
            radius = 50 + (i % 3) * 18
            pygame.draw.ellipse(smoke, (80, 105, 140, 12), (x-radius, y-radius//3, radius*2, radius//2))
        self.screen.blit(smoke, (0, 0))
        if boss_ratio < 0.42 and math.sin(self.elapsed * 18) > 0.55:
            warning = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            warning.fill((180, 0, 20, int(20 + (0.42-boss_ratio)*70)))
            self.screen.blit(warning, (0, 0))

    # ========================================================
    # CHARACTER RENDERING
    # ========================================================

    def _layer(self):
        layer = self._scratch
        layer.fill((0, 0, 0, 0))
        return layer

    def _posed(self, base, facing, scale, sx, sy, rot):
        """Flip / scale / rotate a sprite through a small quantised cache (no per-frame resampling)."""
        sx, sy, scale, rot = round(sx, 2), round(sy, 2), round(scale, 2), int(round(rot))
        key = (id(base), facing, scale, sx, sy, rot)
        cache = self._pose_cache
        img = cache.get(key)
        if img is None:
            if len(cache) > 240:
                cache.clear()
            img = base
            if facing < 0:
                img = pygame.transform.flip(img, True, False)
            if abs(scale - 1) > 0.004:
                img = pygame.transform.smoothscale(
                    img, (max(1, int(base.get_width() * scale)), max(1, int(base.get_height() * scale))))
            if abs(sx - 1) > 0.004 or abs(sy - 1) > 0.004:
                img = pygame.transform.smoothscale(
                    img, (max(1, int(img.get_width() * sx)), max(1, int(img.get_height() * sy))))
            if rot:
                img = pygame.transform.rotate(img, rot)
            cache[key] = img
        return img

    def _aura(self, plain, tint, facing):
        """Soft energy halo built from the sprite's own silhouette (cached)."""
        key = ("aura", tuple(tint), plain.get_size(), facing)
        aura = self._glow_cache.get(key)
        if aura is None:
            pad = AURA_PAD
            w, h = plain.get_size()
            mask = pygame.mask.from_surface(plain, 40)
            sil = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)
            sil.fill((*tint, 0))
            sil.blit(mask.to_surface(setcolor=(*tint, 255), unsetcolor=(*tint, 0)), (pad, pad))
            sw, sh = sil.get_size()
            near = pygame.transform.smoothscale(
                pygame.transform.smoothscale(sil, (max(1, sw // 9), max(1, sh // 9))), (sw, sh))
            far = pygame.transform.smoothscale(
                pygame.transform.smoothscale(sil, (max(1, sw // 24), max(1, sh // 24))), (sw, sh))
            aura = pygame.Surface((sw, sh), pygame.SRCALPHA)
            aura.fill((*tint, 0))
            aura.blit(far, (0, 0))
            aura.blit(near, (0, 0))
            aura.blit(near, (0, 0))
            self._glow_cache[key] = aura
        return aura

    def spawn_ambient_fx(self, dt):
        """Idle aura motes, G.ONE attack trails and RA.ONE dash afterimages."""
        rng = self.rng
        if len(self.particles) < 240:
            for ox, oy, col, alt in ((self.player_x, self.player_y, BLUE, BLUE_WHITE),
                                     (self.boss_x, self.boss_y, RED, RED_WHITE)):
                if rng.random() < 0.5:
                    self.particles.append({
                        "x": ox + rng.uniform(-70, 70), "y": oy - rng.uniform(20, self.sprite_h * 0.9),
                        "vx": rng.uniform(-14, 14), "vy": -rng.uniform(40, 110),
                        "life": rng.uniform(0.5, 0.9), "max": 0.8, "size": rng.randint(2, 3),
                        "color": alt if rng.random() < 0.35 else col})
            if self.attack_time > 0:
                f = self.player_facing
                for _ in range(3):
                    self.particles.append({
                        "x": self.player_x + f * rng.uniform(30, 150),
                        "y": self.player_y - self.hand_h + rng.uniform(-60, 60),
                        "vx": -f * rng.uniform(20, 120), "vy": rng.uniform(-60, 20),
                        "life": 0.35, "max": 0.8, "size": rng.randint(2, 4), "color": BLUE_WHITE})
        self.boss_ghost_timer -= dt
        if self.boss_attack == "dash" and 0 < self.boss_attack_time < 0.48 and self.boss_ghost_timer <= 0:
            self.boss_ghost_timer = 0.04
            self.afterimages.append({"x": self.boss_x, "y": self.boss_y, "f": -1,
                                     "life": 0.3, "max": 0.3, "boss": True})

    def draw_character(
        self,
        image,
        x,
        y,
        tint,
        facing=1,
        alpha=255,
        scale=1.0,
        rot=0.0,
        stretch=(1.0, 1.0),
        flash_img=None,
        flash=0.0,
    ):
        # Uniform scale only: sprites are never squashed or stretched out of proportion.
        uni = math.sqrt(stretch[0] * stretch[1])
        plain = self._posed(image, facing, scale, 1.0, 1.0, 0)
        posed = self._posed(image, facing, scale, uni, uni, rot)
        iw, ih = plain.get_size()
        af = alpha / 255.0
        is_red = tuple(tint) == tuple(RED)
        low = (1.0 - clamp(self.raone_hp / 100.0, 0.0, 1.0)) if is_red else 0.0
        pulse = math.sin(self.elapsed * 3.0)
        cache = self._glow_cache

        # ground light pool + contact shadow
        pkey = ("pool", tuple(tint), iw)
        pool = cache.get(pkey)
        if pool is None:
            pw, ph = int(iw * 2.3), 110
            pool = pygame.Surface((pw, ph), pygame.SRCALPHA)
            for k in range(14):
                f = 1 - k / 14
                pygame.draw.ellipse(pool, (*tint, int(3 + 24 * (k / 14) ** 1.5)),
                                    (pw / 2 - pw / 2 * f, ph / 2 - ph / 2 * f, pw * f, ph * f))
            sh = pygame.Surface((int(iw * 1.0), 26), pygame.SRCALPHA)
            pygame.draw.ellipse(sh, (0, 0, 0, 160), (0, 0, sh.get_width(), 26))
            pool.blit(sh, (pw // 2 - sh.get_width() // 2, ph // 2 - 13))
            cache[pkey] = pool
        pool.set_alpha(max(0, min(255, int((110 + 20 * pulse) * (1 + 0.9 * low) * af))))
        gy = max(y, self.height * 0.72)
        self.screen.blit(pool, (int(x - pool.get_width() / 2), int(gy - pool.get_height() / 2)))

        # floor reflection
        rkey = ("refl", tuple(tint), iw, ih, facing)
        refl = cache.get(rkey)
        if refl is None:
            refl = pygame.transform.smoothscale(pygame.transform.flip(plain, False, True), (iw, max(1, int(ih * 0.30))))
            cache[rkey] = refl
        if y >= self.height * 0.72 - 4:
            refl.set_alpha(int(38 * af))
            self.screen.blit(refl, (int(x - iw / 2), int(y) + 2))

        # blue (G.ONE) / red (RA.ONE) energy aura behind the body
        if is_red:
            boost = 90.0 if self.boss_attack_time > 0 else 0.0
        else:
            boost = (110.0 if self.charge_time > 0 else 0.0) + (50.0 if self.blocking else 0.0)
        aura = self._aura(plain, tint, facing)
        aura.set_alpha(int(clamp(105 + 40 * pulse + 110 * low + boost + 90 * min(1.0, flash), 0, 255) * af))
        self.screen.blit(aura, (int(x - iw / 2) - AURA_PAD, int(y - ih) - AURA_PAD))

        # the character itself
        pos = (int(x - posed.get_width() / 2), int(y - posed.get_height()))
        if alpha != 255:
            posed.set_alpha(alpha)
        self.screen.blit(posed, pos)
        if alpha != 255:
            posed.set_alpha(255)

        # hit flash
        if flash_img is not None and flash > 0.01:
            fimg = self._posed(flash_img, facing, scale, uni, uni, rot)
            fimg.set_alpha(int(255 * min(1.0, flash)))
            self.screen.blit(fimg, pos)
            fimg.set_alpha(255)

    # ========================================================
    # HEALTH AND HUD
    # ========================================================

    def _make_portrait(self, sprite, color, cx_frac, size=76):
        sw, sh = sprite.get_size()
        side = max(8, int(min(sw, sh * 0.21)))
        left = int(clamp(sw * cx_frac - side / 2, 0, max(0, sw - side)))
        crop = pygame.transform.smoothscale(sprite.subsurface((left, 0, side, min(side, sh))).copy(), (size, size))
        mask = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(mask, (255, 255, 255, 255), (size // 2, size // 2), size // 2)
        crop.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        out = pygame.Surface((size + 8, size + 8), pygame.SRCALPHA)
        c = (size + 8) // 2
        pygame.draw.circle(out, (8, 12, 28, 255), (c, c), size // 2 + 2)
        pygame.draw.circle(out, (*color, 70), (c, c), size // 2 + 4, 3)
        out.blit(crop, (4, 4))
        pygame.draw.circle(out, color, (c, c), size // 2 + 2, 3)
        return out

    def _hud_assets(self):
        if self._hud is None:
            w = self.width
            strip = pygame.Surface((w, 124), pygame.SRCALPHA)
            for y in range(124):
                pygame.draw.line(strip, (3, 5, 14, int(225 * (1 - y / 124) ** 1.1)), (0, y), (w, y))
            for x0 in range(0, w, 16):
                pygame.draw.line(strip, (*_side_tint((x0 + 8) / w), 150), (x0, 104), (x0 + 16, 104), 2)
            self._hud = {
                "strip": strip,
                "gone": self._make_portrait(self.gone_img, BLUE, 0.50),
                "raone": self._make_portrait(self.raone_img, RED, 0.54),
            }
        return self._hud

    def draw_health_bar(
        self,
        x,
        y,
        width,
        value,
        color,
        label,
        reverse=False,
        trail=None,
    ):
        scr = self.screen
        h, slant = 24, 14
        inner = width - slant
        ratio = clamp(value / 100.0, 0.0, 1.0)
        tratio = clamp((value if trail is None else trail) / 100.0, 0.0, 1.0)

        def bar(frac):
            fw = int(inner * frac)
            pts = [(x + slant, y), (x + slant + fw, y), (x + fw, y + h), (x, y + h)]
            if reverse:
                pts = [(2 * x + width - px, py) for px, py in pts]
            return pts

        ax = x + width if reverse else x
        draw_text(scr, label, 26, WHITE, (ax, y - 30), center=False, bold=True, right=reverse)
        pygame.draw.polygon(scr, (12, 18, 38), bar(1.0))
        if tratio > ratio:
            pygame.draw.polygon(scr, (255, 214, 110), bar(tratio))
        if ratio > 0:
            fill = color
            if ratio < 0.3:
                k = 0.35 * (0.5 + 0.5 * math.sin(self.elapsed * 9))
                fill = tuple(int(c + (255 - c) * k) for c in color)
            pygame.draw.polygon(scr, fill, bar(ratio))
        pygame.draw.polygon(scr, color, bar(1.0), 2)
        draw_text(scr, f"{int(round(value)):03d} / 100", 13, WHITE, (ax, y + h + 4),
                  center=False, bold=True, right=reverse)

    def _hand_ui_font(self, size, bold=False):
        key = (int(size), bool(bold))
        font = self._hand_panel_font_cache.get(key)
        if font is None:
            font = pygame.font.SysFont("arial", int(size), bold=bold)
            self._hand_panel_font_cache[key] = font
        return font

    def _update_hand_ui(self, camera_state):
        """Cache independent left/right webcam crops; no image conversion per frame."""
        if not camera_state:
            self.hand_ui_state = {}
            self._left_hand_surface = None
            self._right_hand_surface = None
            return
        self.hand_ui_state = camera_state
        frame_id = camera_state.get("camera_frame_id", -1)
        if frame_id == self._hand_ui_frame_id:
            return
        self._hand_ui_frame_id = frame_id
        for key, attr in (("movement_preview", "_left_hand_surface"), ("action_preview", "_right_hand_surface")):
            arr = camera_state.get(key)
            surface = None
            if arr is not None:
                try:
                    # Preview arrays are RGB (height, width, channels).
                    surface = pygame.surfarray.make_surface(arr.swapaxes(0, 1))
                except (ValueError, pygame.error, AttributeError):
                    surface = None
            setattr(self, attr, surface)

    def _draw_hand_camera_panel(self, rect, title, accent, surface, detected, status, confidence=None):
        x, y, w, h = rect
        panel = pygame.Rect(x, y, w, h)
        shadow = pygame.Surface((w + 12, h + 12), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (*accent, 24), (6, 6, w, h), border_radius=10)
        self.screen.blit(shadow, (x - 6, y - 6))
        pygame.draw.rect(self.screen, (7, 12, 25), panel, border_radius=9)
        pygame.draw.rect(self.screen, (*accent, 210), panel, 2, border_radius=9)
        draw_text(self.screen, title, 11, accent, (x + 8, y + 7), center=False, bold=True)
        live_label = "LIVE" if self.hand_ui_state.get("camera_live") else "OFFLINE"
        draw_text(self.screen, live_label, 9, GREEN if live_label == "LIVE" else RED_WHITE,
                  (x + w - 8, y + 8), center=False, bold=True, right=True)
        image_rect = pygame.Rect(x + 7, y + 25, w - 14, h - 49)
        pygame.draw.rect(self.screen, (3, 6, 13), image_rect, border_radius=5)
        if surface is not None and detected:
            fitted = pygame.transform.smoothscale(surface, image_rect.size)
            self.screen.blit(fitted, image_rect.topleft)
        else:
            message = "HAND NOT DETECTED" if self.hand_ui_state.get("camera_live") else "CAMERA OFFLINE"
            draw_text(self.screen, message, 9, MUTED, image_rect.center, bold=True)
        footer = status if detected else ("WAITING FOR HAND" if self.hand_ui_state.get("camera_live") else "NO CAMERA FEED")
        if confidence is not None and detected:
            footer = f"{footer}  {int(max(0, min(1, confidence)) * 100)}%"
        draw_text(self.screen, footer[:30], 9, accent if detected else MUTED,
                  (x + 8, y + h - 17), center=False, bold=True)

    def draw_hand_controls_ui(self):
        """Two separate hand previews at screen edges plus a compact gesture manual."""
        w, h = self.width, self.height
        panel_w = max(150, min(190, int(w * 0.15)))
        panel_h = max(100, min(116, int(h * 0.145)))
        panel_y = 126
        left_detected = bool(self.hand_ui_state.get("movement_hand"))
        right_detected = bool(self.hand_ui_state.get("action_hand"))
        mx = self.hand_ui_state.get("movement_x")
        my = self.hand_ui_state.get("movement_y")
        move_status = "IDLE"
        if left_detected and mx is not None:
            if self.dash_time > 0:
                move_status = "DASH / EVADE"
            elif my is not None and float(my) < 0.23:
                move_status = "DASH / RAISED"
            elif my is not None and 0.23 <= float(my) < 0.38:
                move_status = "JUMP / RAISED"
            elif self.gesture_move_x < 0.43:
                move_status = "MOVE LEFT"
            elif self.gesture_move_x > 0.57:
                move_status = "MOVE RIGHT"
        gesture = self.hand_ui_state.get("action_gesture")
        action_names = {"attack": "ATTACK", "shield": "SHIELD", "heal": "HEAL", "special": "SPECIAL"}
        action_status = action_names.get(str(gesture).lower(), "IDLE") if right_detected else "IDLE"
        confidence = self.hand_ui_state.get("action_confidence", 0.0)
        self._draw_hand_camera_panel((12, panel_y, panel_w, panel_h), "LEFT HAND | MOVEMENT", BLUE,
                                     self._left_hand_surface, left_detected, move_status)
        self._draw_hand_camera_panel((w - panel_w - 12, panel_y, panel_w, panel_h), "RIGHT HAND | COMBAT", RED,
                                     self._right_hand_surface, right_detected, action_status, confidence)

        # Compact two-column manual below the camera previews, kept in the screen margins.
        manual_y = panel_y + panel_h + 9
        manual_h = 116
        gap = 10
        col_w = panel_w
        for x, accent, title, rows in (
            (12, BLUE, "MOVEMENT", ("Hand left/right  >  Move", "Hand raised  >  Jump", "Hand high  >  Dash")),
            (w - panel_w - 12, RED, "COMBAT", ("Fist  >  Attack", "Open palm  >  Shield", "Thumbs up  >  Heal", "Peace  >  Special")),
        ):
            box = pygame.Rect(x, manual_y, col_w, manual_h)
            pygame.draw.rect(self.screen, (6, 10, 21), box, border_radius=8)
            pygame.draw.rect(self.screen, (*accent, 145), box, 1, border_radius=8)
            draw_text(self.screen, title, 10, accent, (x + 8, manual_y + 7), center=False, bold=True)
            line_y = manual_y + 25
            for row in rows:
                draw_text(self.screen, row, 9, WHITE, (x + 8, line_y), center=False)
                line_y += 19

    def draw_hud(self):
        hud = self._hud_assets()
        scr, w = self.screen, self.width
        cx = w // 2
        dt = clamp(self.clock.get_time() / 1000.0, 0.0, 0.05)
        for key, hp in (("g", self.gone_hp), ("r", self.raone_hp)):
            cur = self.hp_trail[key]
            self.hp_trail[key] = hp if hp >= cur else cur + (hp - cur) * min(1.0, dt * 2.2)

        scr.blit(hud["strip"], (0, 0))
        bar_w = int(w * 0.30)
        scr.blit(hud["gone"], hud["gone"].get_rect(center=(56, 60)))
        scr.blit(hud["raone"], hud["raone"].get_rect(center=(w - 56, 60)))
        self.draw_health_bar(104, 44, bar_w, self.gone_hp, BLUE, "G.ONE", False, self.hp_trail["g"])
        self.draw_health_bar(w - 104 - bar_w, 44, bar_w, self.raone_hp, RED, "RA.ONE", True, self.hp_trail["r"])

        # timer plate
        plate = [(cx - 84, 20), (cx + 84, 20), (cx + 100, 44), (cx + 84, 68), (cx - 84, 68), (cx - 100, 44)]
        pygame.draw.polygon(scr, (7, 12, 30), plate)
        pygame.draw.polygon(scr, BLUE_WHITE, plate, 2)
        secs = int(math.ceil(max(0.0, FIGHT_SECONDS - self.fight_time)))
        tcol = RED_WHITE if (secs <= 20 and int(self.elapsed * 4) % 2) else WHITE
        draw_text(scr, f"{secs // 60:02d}:{secs % 60:02d}", 40, tcol, (cx, 45), bold=True)
        draw_text(scr, "FINAL PROTOCOL", 12, MUTED, (cx, 10), bold=True)

        # boss phase / Omega Mode
        if self.boss_phase == 2:
            col = RED if int(self.elapsed * 6) % 2 else RED_WHITE
            draw_text(scr, "PHASE 2/2  //  OMEGA MODE", 15, col, (cx, 86), bold=True)
        else:
            draw_text(scr, "PHASE 1/2  //  STABLE", 14, MUTED, (cx, 86), bold=True)

        # special energy meter (35 needed for the blast)
        ex, ey, ew = 104, 92, int(bar_w * 0.62)
        ready = self.energy >= 35
        pygame.draw.rect(scr, (16, 26, 44), (ex, ey, ew, 10), border_radius=4)
        fillc = BLUE_WHITE if (ready and int(self.elapsed * 6) % 2) else BLUE
        pygame.draw.rect(scr, fillc, (ex, ey, int(ew * clamp(self.energy / 100.0, 0, 1)), 10), border_radius=4)
        pygame.draw.line(scr, WHITE, (ex + int(ew * 0.35), ey - 2), (ex + int(ew * 0.35), ey + 12), 2)
        pygame.draw.rect(scr, BLUE, (ex, ey, ew, 10), 1, border_radius=4)
        draw_text(scr, "ENERGY  READY" if ready else f"ENERGY  {int(self.energy)}%", 12,
                  BLUE_WHITE if ready else MUTED, (ex + ew + 10, ey - 1), center=False, bold=True)

        # RA.ONE AI action readout
        if self.ai_label and self.elapsed < self.ai_label_until and self.ppo_model is not None:
            draw_text(scr, f"RA.ONE AI: {self.ai_label}", 13, RED_WHITE, (w - 104, 90), center=False,
                      bold=True, right=True)

        # combo counter
        if self.combo > 1 and self.combo_timer > 0:
            size = 30 + int(14 * clamp(self.combo_pulse / 0.35, 0, 1))
            draw_text(scr, f"{self.combo}x COMBO", size, GOLD, (30, 122), center=False, bold=True)
            pygame.draw.rect(scr, GOLD, (32, 122 + size + 2, int(150 * clamp(self.combo_timer / 2.4, 0, 1)), 4),
                             border_radius=2)

        footer = (
            "A/D MOVE   W JUMP   J ATTACK   "
            "K BLOCK   L ENERGY BLAST   SPACE DASH"
        )
        draw_text(self.screen, footer, 13, (154, 176, 199), (self.width // 2, self.height - 22), bold=True)
        control_hint = "LEFT HAND: MOVE / RAISE: JUMP-DASH   RIGHT: FIST ATTACK | PALM SHIELD | THUMB HEAL | PEACE SPECIAL"
        draw_text(self.screen, control_hint, 12, (125, 190, 220), (self.width // 2, self.height - 43), bold=True)
        draw_text(self.screen, self.gesture_status, 11,
                  (90, 235, 170) if self.camera_controls_live else (255, 170, 100),
                  (self.width // 2, self.height - 60), bold=True)

    # ========================================================
    # EFFECT RENDERING
    # ========================================================

    def draw_effects(self):

        if self.rings:
            layer = self._layer()
            for ring in self.rings:
                alpha = int(clamp(200 * ring["life"] / 0.45, 0, 255))
                pygame.draw.circle(layer, (*ring["color"], alpha),
                                   (int(ring["x"]), int(ring["y"])), int(ring["r"]), 3)
            self.screen.blit(layer, (0, 0))

        for particle in self.particles:

            pygame.draw.circle(
                self.screen,
                particle["color"],
                (
                    int(particle["x"]),
                    int(particle["y"]),
                ),
                max(1, particle["size"]),
            )

        # RA.ONE overdrive aura ramps up as his health falls.
        ratio = clamp(self.raone_hp / 100.0, 0.0, 1.0)
        if ratio < 0.72:
            aura = self._layer()
            cx, cy = int(self.boss_x), int(self.boss_y - self.chest_h)
            intensity = 1.0 - ratio
            pulse = 5 + int(8 * abs(math.sin(self.elapsed * 9)))
            pygame.draw.circle(aura, (255, 20, 50, int(18 + intensity * 30)), (cx, cy), int(95 + intensity * 45 + pulse))
            for j in range(5):
                ang = self.elapsed * (1.5 + j * .12) + j * math.tau / 5
                ex = cx + int(math.cos(ang) * (90 + intensity * 50))
                ey = cy + int(math.sin(ang) * (110 + intensity * 35))
                pygame.draw.line(aura, (255, 30, 55, 175), (cx, cy), (ex, ey), 2)
            self.screen.blit(aura, (0, 0))

        self.draw_energy_fx()

        if self.flash > 0:

            self._flash_surf.set_alpha(int(80 * self.flash))
            self.screen.blit(self._flash_surf, (0, 0))

    def add_hit_flash(self, x, y, color):
        hf = getattr(self, "hit_flashes", None)
        if hf is None:
            hf = self.hit_flashes = []
        hf.append([x, y, color, 0.18])
        self.create_ring(x, y, color, 70)
        self.particle_burst(x, y, (255, 225, 170), 7, 300)

    def draw_energy_fx(self):
        """Attack-only discharges, low-health smoke and hit flashes."""
        if not (
            self.boss_attack_time > 0
            or getattr(self, "hit_flashes", None)
            or getattr(self, "smoke", None)
            or self.raone_hp < 70
        ):
            return
        layer = self._layer()
        low = 1.0 - clamp(self.raone_hp / 100.0, 0.0, 1.0)
        if self.boss_attack_time > 0 and self.rng.random() < 0.55:
            d = -1 if self.player_x < self.boss_x else 1
            px, py = self.boss_x + d * 70, self.boss_y - self.hand_h
            pts = [(px, py)]
            for _ in range(6):
                px += d * self.rng.randint(14, 32)
                py += self.rng.randint(-26, 30)
                pts.append((px, py))
                if self.rng.random() < 0.35:
                    br = [(px, py), (px + d * 14, py + self.rng.randint(-24, 24)), (px + d * 28, py + self.rng.randint(-30, 30))]
                    pygame.draw.lines(layer, (*RED, 190), False, br, 1)
            pygame.draw.lines(layer, (*RED, 70), False, pts, 5)
            pygame.draw.lines(layer, (*RED, 220), False, pts, 2)
            pygame.draw.lines(layer, (255, 235, 235, 255), False, pts, 1)
        smoke = getattr(self, "smoke", None)
        if smoke is None:
            smoke = self.smoke = []
        if low > 0.3 and self.rng.random() < 0.25 + low * 0.4 and len(smoke) < 40:
            smoke.append([self.boss_x + self.rng.randint(-40, 40), self.boss_y - 40, self.rng.uniform(0.8, 1.6), 1.6])
        for p in smoke[:]:
            p[1] -= 38 * 0.016
            p[0] += math.sin(p[1] * 0.05) * 0.4
            p[2] -= 0.016
            if p[2] <= 0:
                smoke.remove(p)
                continue
            pygame.draw.circle(layer, (25, 12, 20, int(70 * p[2] / p[3])), (int(p[0]), int(p[1])), int(14 + (p[3] - p[2]) * 14))
        for h in getattr(self, "hit_flashes", [])[:]:
            h[3] -= 0.016
            if h[3] <= 0:
                self.hit_flashes.remove(h)
                continue
            k = h[3] / 0.18
            for r, al in ((70, 60), (42, 120), (20, 230)):
                pygame.draw.circle(layer, (*h[2], int(al * k)), (int(h[0]), int(h[1])), int(r * (1.3 - 0.3 * k)))
            for n in range(8):
                ang = n * math.tau / 8 + h[0] * 0.01
                r0, r1 = 10, 26 + 46 * (1 - k)
                pygame.draw.line(layer, (255, 255, 255, int(230 * k)),
                                 (int(h[0] + math.cos(ang) * r0), int(h[1] + math.sin(ang) * r0)),
                                 (int(h[0] + math.cos(ang) * r1), int(h[1] + math.sin(ang) * r1)), 2)
            pygame.draw.circle(layer, (255, 255, 255, int(255 * k)), (int(h[0]), int(h[1])), int(12 * k) + 2)
        self.screen.blit(layer, (0, 0))

    # ========================================================
    # DAMAGE SYSTEM
    # ========================================================

    def damage_player(self, amount):

        if self.player_invuln > 0 or self.outcome:
            return False

        blocked = self.blocking
        px, py = self.player_x, self.player_y - self.chest_h

        if blocked:
            amount *= 0.22
            self.energy = min(100, self.energy + 8)
            self.shield_hit = 0.35
            self.shield_hit_pos = (px + self.player_facing * 60, py)
            self.create_ring(*self.shield_hit_pos, BLUE_WHITE, 95)
            self.particle_burst(*self.shield_hit_pos, BLUE_WHITE, 16, 260)
            self.floater(px, py - 120, "BLOCKED!", BLUE_WHITE, 26)
            self.shake = max(self.shake, 3)
            self.play("shield")
        else:
            self.player_hit = 0.3
            self.combo = 0
            self.combo_timer = 0.0
            self.player_kb = -self.player_facing * 260
            self.flash = 0.5
            self.shake = max(self.shake, 9)
            self.hitstop = max(self.hitstop, 0.05)
            self.add_hit_flash(px, py, RED)
            self.particle_burst(px, py, RED, 18, 220)
            self.create_ring(px, py, RED, 120)
            self.floater(px, py - 120, f"-{int(round(amount))}", RED, 32)
            self.play("hit")

        self.gone_hp = max(0, self.gone_hp - amount)
        self.player_invuln = 0.65

        if self.gone_hp <= 0 and self.outcome is None:
            self.outcome = "defeat"
        return True

    def damage_boss(self, amount, big=False):

        if self.outcome:
            return False

        bx, by = self.boss_x, self.boss_y - self.chest_h

        if self.boss_shield > 0:
            self.create_ring(bx - 80, by, RED_WHITE, 100)
            self.particle_burst(bx - 80, by, RED_WHITE, 14, 240)
            self.floater(bx, by - 150, "BLOCKED!", RED_WHITE, 26)
            self.play("shield")
            return False

        if self.boss_stun > 0:
            amount *= 1.25

        self.raone_hp = max(0, self.raone_hp - amount)
        self.combo += 2 if big else 1
        self.combo_timer = 2.4
        self.combo_pulse = 0.35
        self.boss_stun = 0.35 if big else 0.25
        self.boss_hit = 0.4 if big else 0.22
        away = 1 if self.boss_x >= self.player_x else -1
        self.boss_kb = away * (300 if big else 170)
        self.hitstop = max(self.hitstop, 0.09 if big else 0.05)
        self.shake = max(self.shake, 13 if big else 6)

        self.add_hit_flash(bx, by, BLUE)
        self.add_impact(bx, by, BLUE_WHITE, 220 if big else 130, 0.4 if big else 0.28)
        self.particle_burst(bx, by, BLUE_WHITE, 46 if big else 22, 460 if big else 300)
        self.particle_burst(bx, by, BLUE, 30 if big else 14, 260)
        self.create_ring(bx, by, BLUE, 260 if big else 120)
        if big:
            self.create_ring(bx, by, BLUE_WHITE, 170)
            self.create_ring(bx, self.boss_y, BLUE, 280)
            self.flash = 0.8
        self.floater(bx, by - 150, f"-{int(round(amount))}", BLUE_WHITE, 34 if big else 28)
        self.play("boom" if big else "hit")

        if self.raone_hp <= 0 and self.outcome is None:
            self.outcome = "victory"
        return True

    def update_boss_ppo(self, dt):

        for name in ("b_atk_cd", "b_shield_cd", "b_heal_cd", "b_spec_cd"):
            setattr(self, name, max(0.0, getattr(self, name) - dt))
        self.boss_shield = max(0.0, self.boss_shield - dt)
        self.boss_cast_vis = max(0.0, self.boss_cast_vis - dt)

        if self.boss_bolt_delay > 0:
            self.boss_bolt_delay -= dt
            if self.boss_bolt_delay <= 0:
                self.launch_boss_bolt()

        if self.boss_stun > 0 or self.boss_attack_time > 0:
            return

        if self.ppo_model is None:
            if self.boss_attack_timer <= 0:
                self.begin_boss_attack()
            return

        self.ppo_acc += dt
        if self.ppo_acc < 0.1:
            return
        self.ppo_acc = 0.0

        try:
            import numpy as np
            obs = np.asarray([
                self.raone_hp / 100.0,
                self.gone_hp / 100.0,
                min(1.0, self.boss_shield / 2.4),
                1.0 if self.blocking else 0.0,
                min(1.0, self.b_atk_cd / 0.9),
                min(1.0, self.b_shield_cd / 5.0),
                min(1.0, self.b_heal_cd / 7.0),
                min(1.0, self.b_spec_cd / 4.0),
                min(1.0, self.g_attack_timer / 3.5),
                min(1.0, max(0.0, 1.0 - self.fight_time / 99.0)),
            ], dtype=np.float32)
            action, _ = self.ppo_model.predict(obs, deterministic=True)
            action = int(np.asarray(action).item())
        except Exception as exc:
            print(f"Final Protocol: PPO inference failed, scripted fallback: {exc}")
            self.ppo_model = None
            return

        pressure = 0.8 if self.raone_hp <= 45 else 1.0
        labels = {0: "WAIT", 1: "ATTACK", 2: "SHIELD", 3: "HEAL", 4: "SPECIAL"}
        self.ai_label = labels.get(action, "WAIT")
        self.ai_label_until = self.elapsed + 1.2
        bx, by = self.boss_x, self.boss_y - self.chest_h

        if action == 1 and self.b_atk_cd <= 0:
            # PPO ATTACK becomes a close-range rush when RA.ONE has reached G.ONE.
            # At longer range he fires a projectile while continuing to close the gap.
            distance = abs(self.player_x - self.boss_x)
            if distance > 245:
                self.boss_bolt_delay = 0.12
                self.boss_cast_vis = 0.35
            else:
                self.boss_attack = "dash"
                self.boss_phase = 2 if self.raone_hp <= 45 else 1
                self.boss_attack_time = 0.58
                self.boss_attack_timer = 0.9
                self.particle_burst(self.boss_x, self.boss_y - self.chest_h, RED, 14, 180)
                self.shake = max(self.shake, 4)
            self.b_atk_cd = 1.15 * pressure

        elif action == 2 and self.b_shield_cd <= 0:
            self.boss_shield = 2.4
            self.b_shield_cd = 5.0 * pressure
            self.create_ring(bx, by, RED, 180)
            self.particle_burst(bx, by, RED, 18, 240)

        elif action == 3 and self.b_heal_cd <= 0 and self.raone_hp < 100:
            before = self.raone_hp
            self.raone_hp = min(100, self.raone_hp + 5)
            self.b_heal_cd = 7.0 * pressure
            self.particle_burst(bx, by, (80, 255, 140), 18, 150)
            self.floater(bx, by - 150, f"+{int(self.raone_hp - before)} HP", (80, 255, 140), 26)

        elif action == 4 and self.b_spec_cd <= 0:
            self.begin_boss_attack()
            if self.boss_attack_time > 0:
                self.b_spec_cd = 4.0 * pressure
                self.b_atk_cd = max(self.b_atk_cd, 0.9 * pressure)

    def launch_boss_bolt(self):

        sx = self.boss_x - 90
        sy = self.boss_y - self.hand_h
        ty = self.player_y - self.chest_h
        dist = max(60.0, abs(self.player_x - sx))
        speed = 1000.0
        direction = -1 if self.player_x < sx else 1
        self.projectiles.append({
            "x": sx, "y": sy, "vx": direction * speed,
            "vy": (ty - sy) / (dist / speed), "owner": "raone",
            "damage": 6, "size": 14,
        })
        self.add_impact(sx, sy, RED, 90, 0.2)
        self.play("bolt")

    def update_projectiles(self, dt):

        for p in self.projectiles[:]:

            p["x"] += p["vx"] * dt
            p["y"] += p.get("vy", 0.0) * dt
            mine = p["owner"] == "gone"
            color = BLUE_WHITE if mine else RED_WHITE
            big = p.get("big", False)

            # glowing particle trail
            for _ in range(3 if big else 1):
                self.particles.append({
                    "x": p["x"] - p["vx"] * 0.01, "y": p["y"] + self.rng.uniform(-10, 10) * (2 if big else 1),
                    "vx": -p["vx"] * 0.05 + self.rng.uniform(-30, 30), "vy": self.rng.uniform(-40, 40),
                    "life": 0.35, "max": 0.8, "size": self.rng.randint(2, 5),
                    "color": BLUE if mine else RED,
                })

            if p["x"] < -80 or p["x"] > self.width + 80:
                self.projectiles.remove(p)
                continue

            if mine:
                if abs(p["x"] - self.boss_x) < 75 and abs(p["y"] - (self.boss_y - self.chest_h)) < 180:
                    self.projectiles.remove(p)
                    if self.boss_shield <= 0:
                        self.damage_boss(p["damage"], big=big)
                    else:
                        self.damage_boss(p["damage"])
            else:
                if abs(p["x"] - self.player_x) < 50 and abs(p["y"] - (self.player_y - self.chest_h)) < 140:
                    if self.player_invuln > 0 and not self.blocking:
                        continue        # dashed / i-framed through it
                    self.projectiles.remove(p)
                    self.damage_player(p["damage"])

    def update_fx(self, dt):

        for a in self.afterimages[:]:
            a["life"] -= dt
            if a["life"] <= 0:
                self.afterimages.remove(a)
        for f in self.floaters[:]:
            f[1] -= 55 * dt
            f[4] -= dt
            if f[4] <= 0:
                self.floaters.remove(f)
        for i in self.impacts[:]:
            i["life"] -= dt
            if i["life"] <= 0:
                self.impacts.remove(i)

    # ========================================================
    # RA.ONE AI
    # ========================================================

    def begin_boss_attack(self):

        if (
            self.boss_stun > 0
            or self.boss_attack_time > 0
        ):
            return

        phase2 = self.raone_hp <= 45

        self.boss_phase = 2 if phase2 else 1

        options = (
            ["slam", "dash", "pulse", "pulse"]
            if phase2
            else ["slam", "dash", "pulse"]
        )

        self.boss_attack = self.rng.choice(options)

        if self.atmosphere is not None:
            self.atmosphere.lightning.strike(1.0, ground=self.boss_attack in ("slam", "pulse"))

        if self.boss_attack == "pulse":
            self.boss_attack_time = 0.85
        else:
            self.boss_attack_time = 0.72

        self.boss_attack_timer = (
            self.rng.uniform(1.0, 1.65)
            if phase2
            else self.rng.uniform(1.4, 2.1)
        )

        self.particle_burst(
            self.boss_x,
            self.boss_y - self.chest_h,
            RED,
            8,
            90,
        )

    def update_boss_attack(self, dt):

        if self.boss_attack_time <= 0:
            return

        self.boss_attack_time -= dt

        t = self.boss_attack_time

        if self.boss_attack == "slam":

            if 0.35 < t < 0.42:

                self.create_ring(
                    self.boss_x,
                    self.height * BG_FEET_ANCHOR,
                    RED,
                    230,
                )

            if t <= 0.32 < t + dt:

                if (
                    abs(self.player_x - self.boss_x) < 205
                    and abs(
                        self.player_y - self.height * 0.72
                    ) < 100
                ):

                    damage = (
                        24 if self.boss_phase == 1
                        else 31
                    )

                    self.damage_player(damage)

                self.shake = max(
                    self.shake,
                    13,
                )

                self.particle_burst(
                    self.boss_x,
                    self.height * BG_FEET_ANCHOR,
                    RED,
                    25,
                    260,
                )

        elif self.boss_attack == "dash":

            if t < 0.48:

                direction = (
                    -1
                    if self.boss_x > self.player_x
                    else 1
                )

                speed = (
                    680 if self.boss_phase == 2
                    else 560
                )

                self.boss_x += direction * speed * dt

                self.boss_x = clamp(
                    self.boss_x,
                    self.width * 0.53,
                    self.width - 100,
                )

            if t <= 0.40 < t + dt:

                if abs(
                    self.player_x - self.boss_x
                ) < 120:

                    self.damage_player(28)

        elif self.boss_attack == "pulse":

            if t <= 0.48 < t + dt:

                self.create_ring(
                    self.boss_x,
                    self.boss_y - self.chest_h,
                    RED,
                    330,
                )

                if (
                    abs(self.player_x - self.boss_x) < 250
                    and self.dash_time <= 0
                ):

                    self.damage_player(20)

                self.particle_burst(
                    self.boss_x,
                    self.boss_y - self.chest_h,
                    RED,
                    30,
                    300,
                )

        if self.boss_attack_time <= 0:
            self.boss_attack = None

    def draw_fx(self):
        """Cinematic attack visuals drawn on one shared alpha layer."""

        L = self.fx_layer
        L.fill((0, 0, 0, 0))
        f = self.player_facing
        hand = (int(self.player_x + f * 70), int(self.player_y - self.hand_h))

        # J: fast blue energy slash arc following the swing progress
        if self.attack_time > 0:
            p = 1 - self.attack_time / 0.24
            cx, cy = self.player_x + f * 30, self.player_y - self.hand_h
            pts = []
            for i in range(11):
                a = math.radians(-85 + 135 * (p * i / 10))
                r = 120 + 20 * math.sin(i / 10 * math.pi)
                pts.append((cx + f * r * math.cos(a), cy + r * math.sin(a)))
            if len(pts) > 1:
                pygame.draw.lines(L, (47, 140, 255, 80), False, pts, 22)
                pygame.draw.lines(L, (95, 200, 255, 190), False, pts, 9)
                pygame.draw.lines(L, (235, 250, 255, 255), False, pts, 3)

        # L: charging orb with converging sparks
        if self.charge_time > 0:
            k = 1 - self.charge_time / 0.40
            rad = int(10 + 38 * k)
            for r, a in ((rad + 22, 40), (rad + 8, 90), (rad, 200)):
                pygame.draw.circle(L, (80, 190, 255, a), hand, r)
            pygame.draw.circle(L, (235, 250, 255, 255), hand, max(4, rad // 2))
            for _ in range(3):
                ang = self.rng.uniform(0, math.tau)
                d = 90 * (1 - k) + 20
                pygame.draw.line(L, (150, 225, 255, 220),
                                 (hand[0] + math.cos(ang) * d, hand[1] + math.sin(ang) * d),
                                 (hand[0] + math.cos(ang) * d * 0.6, hand[1] + math.sin(ang) * d * 0.6), 2)
        elif self.special_time > 0:
            pygame.draw.circle(L, (200, 240, 255, 200), hand, int(45 * self.special_time / 0.3) + 10)

        # Projectiles: layered glow cores
        for p in self.projectiles:
            c = (int(p["x"]), int(p["y"]))
            s = p["size"] + (4 if int(self.elapsed * 30) % 2 else 0)
            mine = p["owner"] == "gone"
            glow = (47, 160, 255) if mine else (255, 48, 69)
            core = (235, 250, 255) if mine else (255, 200, 205)
            tail = (c[0] - int(p["vx"] * 0.06), c[1] - int(p.get("vy", 0) * 0.06))
            pygame.draw.line(L, (*glow, 90), tail, c, s * 2)
            pygame.draw.circle(L, (*glow, 70), c, s * 3)
            pygame.draw.circle(L, (*glow, 160), c, int(s * 1.7))
            pygame.draw.circle(L, (*core, 255), c, s)

        # Impact flashes (starburst)
        for i in self.impacts:
            k = i["life"] / i["max"]
            r = int(i["r"] * (1.15 - 0.6 * k))
            c = (int(i["x"]), int(i["y"]))
            pygame.draw.circle(L, (*i["color"], int(150 * k)), c, r)
            pygame.draw.circle(L, (255, 255, 255, int(230 * k)), c, max(2, r // 3))
            for j in range(8):
                ang = j * math.tau / 8 + i["max"] * 5
                pygame.draw.line(L, (255, 255, 255, int(220 * k)), c,
                                 (c[0] + math.cos(ang) * r * 1.3, c[1] + math.sin(ang) * r * 1.3), 3)

        # K: player energy shield with ripple
        if self.shield_vis > 0.02:
            v = self.shield_vis
            cx, cy = int(self.player_x), int(self.player_y - self.chest_h)
            rx, ry = int(110 * self.k * (0.6 + 0.4 * v)), int(210 * self.k * (0.6 + 0.4 * v))
            rect = pygame.Rect(cx - rx, cy - ry, rx * 2, ry * 2)
            boost = 70 if self.shield_hit > 0 else 0
            pygame.draw.ellipse(L, (65, 185, 255, int(55 * v) + boost), rect)
            pygame.draw.ellipse(L, (139, 224, 255, int(220 * v)), rect, 3)
            for j in range(3):
                yy = cy + int(math.sin(self.elapsed * 4 + j * 2.1) * ry * 0.7)
                half = int(rx * math.sqrt(max(0.0, 1 - ((yy - cy) / ry) ** 2)))
                pygame.draw.line(L, (170, 235, 255, int(90 * v)), (cx - half, yy), (cx + half, yy), 1)
            if self.shield_hit > 0:
                k = 1 - self.shield_hit / 0.35
                hx, hy = int(self.shield_hit_pos[0]), int(self.shield_hit_pos[1])
                pygame.draw.circle(L, (235, 250, 255, int(230 * (1 - k))), (hx, hy), int(12 + 70 * k), 3)
                pygame.draw.circle(L, (139, 224, 255, int(160 * (1 - k))), (hx, hy), int(6 + 40 * k), 2)

        # RA.ONE shield dome
        if self.boss_shield > 0:
            cx, cy = int(self.boss_x), int(self.boss_y - self.chest_h)
            a = 150 if self.boss_shield > 0.4 else int(150 * self.boss_shield / 0.4)
            rect = pygame.Rect(cx - int(150 * self.k), cy - int(240 * self.k), int(300 * self.k), int(480 * self.k))
            pygame.draw.ellipse(L, (255, 48, 69, a // 3), rect)
            pygame.draw.ellipse(L, (255, 140, 150, a), rect, 3)

        self.screen.blit(L, (0, 0))

        for fl in self.floaters:
            draw_text(self.screen, fl[2], fl[5], fl[3], (fl[0], fl[1]), bold=True)

    def draw_boss_warning(self):

        # Only powerful attacks get a warning, placed above RA.ONE.
        if self.boss_attack_time > 0 and self.boss_attack in ("slam", "pulse"):

            label = "IMPACT WARNING" if self.boss_attack == "slam" else "ENERGY SURGE"
            color = RED if int(self.elapsed * 12) % 2 else RED_WHITE

            draw_text(
                self.screen,
                label,
                16,
                color,
                (int(self.boss_x), max(104, int(self.boss_y - self.sprite_h - 26))),
                bold=True,
            )

    # ========================================================
    # CINEMATIC INTRO
    # ========================================================

    def intro(self):

        duration = 6.2
        t = 0.0

        while t < duration:

            dt = min(
                self.clock.tick(60) / 1000,
                0.04,
            )

            t += dt
            self.elapsed += dt

            for event in pygame.event.get():

                if event.type == pygame.QUIT:
                    self.stop_music()
                    return False

                if (
                    event.type == pygame.KEYDOWN
                    and event.key == pygame.K_ESCAPE
                ):

                    self.stop_music()
                    return False

            self.draw_background()

            bar = int(
                self.height * 0.13
                * min(1, t * 2)
            )

            pygame.draw.rect(
                self.screen,
                (0, 0, 0),
                (0, 0, self.width, bar),
            )

            pygame.draw.rect(
                self.screen,
                (0, 0, 0),
                (
                    0,
                    self.height - bar,
                    self.width,
                    bar,
                ),
            )

            alpha = int(
                255 * clamp(
                    (t - 0.4) / 1.2,
                    0,
                    1,
                )
            )

            if t < 1.7:

                draw_text(
                    self.screen,
                    "WARNING // REALITY BREACH",
                    25,
                    RED,
                    (self.width // 2, self.height * 0.38),
                    bold=True,
                )
                draw_text(
                    self.screen,
                    "UNKNOWN ENTITY HAS ENTERED THE CORE",
                    16,
                    RED_WHITE,
                    (self.width // 2, self.height * 0.44),
                )

            elif t < 3.7:

                self.draw_character(
                    self.raone_img,
                    self.width * 0.72,
                    self.height * BG_FEET_ANCHOR,
                    RED,
                    -1,
                    alpha,
                    scale=1.08,
                )

                draw_text(
                    self.screen,
                    "RA.ONE // ASCENSION PROTOCOL",
                    28,
                    RED_WHITE,
                    (
                        self.width * 0.5,
                        self.height * 0.23,
                    ),
                    bold=True,
                )

            else:

                self.draw_character(
                    self.gone_img,
                    self.width * 0.27,
                    self.height * BG_FEET_ANCHOR,
                    BLUE,
                    1,
                    alpha,
                )

                self.draw_character(
                    self.raone_img,
                    self.width * 0.73,
                    self.height * BG_FEET_ANCHOR,
                    RED,
                    -1,
                    alpha,
                )

                draw_text(
                    self.screen,
                    "ONE LAST FIGHT",
                    38,
                    WHITE,
                    (
                        self.width // 2,
                        self.height * 0.24,
                    ),
                    bold=True,
                )

                draw_text(
                    self.screen,
                    "G.ONE  VS  RA.ONE",
                    22,
                    BLUE_WHITE,
                    (
                        self.width // 2,
                        self.height * 0.31,
                    ),
                    bold=True,
                )

            # A brief red-white core detonation makes the boss reveal land like a shock.
            if 3.55 < t < 3.82:
                pulse = int(150 * (1 - abs(t - 3.685) / 0.135))
                flash = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
                flash.fill((255, 20, 45, max(0, pulse)))
                self.screen.blit(flash, (0, 0))
            if t > 4.0:
                warning = "THREAT LEVEL: OMEGA"
                draw_text(self.screen, warning, 17, RED_WHITE,
                          (self.width // 2, self.height * 0.31), bold=True)

            pygame.display.flip()

        return True

    # ========================================================
    # ENDING
    # ========================================================

    def aftermath(self, outcome):
        """Keep the battlefield on screen; only a subtle indicator near the top."""

        victory = outcome == "victory"
        color = BLUE if victory else RED
        t = 0.0
        floor = self.height * BG_FEET_ANCHOR

        while True:

            dt = min(self.clock.tick(60) / 1000, 0.035)
            t += dt
            self.elapsed += dt

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.exit_requested = True
                    return outcome
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.exit_requested = True
                    return outcome

            # Automatically hand off to the post-bonus ending video.
            # No Enter/Space press is required after the bonus fight.
            if t >= 2.5:
                return outcome

            self.player_hit = max(0, self.player_hit - dt)
            self.boss_hit = max(0, self.boss_hit - dt)
            self.shield_hit = max(0, self.shield_hit - dt)
            self.player_x = clamp(self.player_x + self.player_kb * dt, 80, self.width - 80)
            self.player_kb *= max(0.0, 1 - 9 * dt)
            self.boss_x = clamp(self.boss_x + self.boss_kb * dt, self.width * 0.45, self.width - 100)
            self.boss_kb *= max(0.0, 1 - 9 * dt)
            self.projectiles.clear()
            self.boss_shield = 0
            self.blocking = False
            self.shield_vis = 0
            self.attack_time = self.charge_time = self.special_time = 0

            loser_x, loser_y = (
                (self.boss_x, self.boss_y) if victory else (self.player_x, self.player_y)
            )
            if self.rng.random() < 0.5:
                self.particle_burst(loser_x + self.rng.randint(-60, 60),
                                    loser_y - self.rng.randint(60, 240),
                                    RED if victory else BLUE, 1, 90)

            self.update_effects(dt)
            self.update_fx(dt)
            self.draw_background()

            fall = clamp(t / 0.8, 0, 1)
            self.draw_character(
                self.gone_img, self.player_x, self.player_y, BLUE, self.player_facing,
                255 if victory else int(255 - 115 * fall), 1.0,
                rot=0 if victory else self.player_facing * 22 * fall,
            )
            self.draw_character(
                self.raone_img, self.boss_x, self.boss_y, RED, -1,
                int(255 - 115 * fall) if victory else 255, 1.0,
                rot=-22 * fall if victory else 0,
            )

            self.draw_effects()
            self.draw_fx()
            self.draw_hud()

            vis = clamp((t - 0.5) / 0.8, 0, 1)
            if vis > 0:
                col = tuple(int(c * vis + 20 * (1 - vis)) for c in color)
                draw_text(self.screen, "VICTORY" if victory else "DEFEAT", 24, col,
                          (self.width // 2, 78), bold=True)
                draw_text(self.screen, "ENTER: CONTINUE   ESC: EXIT", 12,
                          tuple(int(c * vis) for c in MUTED), (self.width // 2, 100), bold=True)

            pygame.display.flip()

    def handle_gesture_action(self, gesture):
        """Apply a confirmed right-hand gesture once per gesture transition."""
        if gesture == "shield":
            return  # Shield is held continuously while the gesture is recognised.
        if gesture == self.last_camera_gesture:
            return
        self.last_camera_gesture = gesture

        if gesture == "attack" and self.attack_cooldown <= 0 and self.dash_time <= 0:
            if abs(self.boss_x - self.player_x) < 340:
                self.player_facing = 1 if self.boss_x >= self.player_x else -1
            self.attack_time = 0.24
            self.attack_cooldown = 0.38
            self.attack_pending = True
            self.last_player_action = self.elapsed
            self.play("swing")
        elif gesture == "special" and self.energy >= 35 and self.special_cooldown <= 0 and self.charge_time <= 0:
            self.energy -= 35
            self.charge_time = 0.40
            self.special_cooldown = 1.25
            self.last_player_action = self.elapsed
            self.play("charge")
        elif gesture == "heal" and self.player_heal_cooldown <= 0 and self.gone_hp < 100:
            before = self.gone_hp
            self.gone_hp = min(100.0, self.gone_hp + 14.0)
            healed = self.gone_hp - before
            self.player_heal_cooldown = 6.0
            if healed > 0:
                self.floater(self.player_x, self.player_y - 210, f"+{int(healed)} HP", GREEN, 28)
                self.particle_burst(self.player_x, self.player_y - 80, GREEN, 18, 180)
                self.create_ring(self.player_x, self.player_y - 80, GREEN, 125)
                for _ in range(26):
                    self.particles.append({"x": self.player_x + self.rng.uniform(-70, 70),
                                           "y": self.player_y - self.rng.uniform(0, 140),
                                           "vx": self.rng.uniform(-20, 20), "vy": -self.rng.uniform(120, 260),
                                           "life": self.rng.uniform(0.7, 1.1), "max": 0.8,
                                           "size": self.rng.randint(2, 4), "color": GREEN})
                self.play("shield")

    # ========================================================
    # MAIN GAME LOOP
    # ========================================================

    def run(self):

        if not self.intro():
            self.exit_requested = True
            return "defeat"

        if not self.select_player_character():
            return "defeat"

        floor = self.height * BG_FEET_ANCHOR
        self.player_y = floor
        self.boss_y = floor
        ATTACK_TIME = 0.24
        CHARGE_TIME = 0.40

        while True:

            dt = min(self.clock.tick(60) / 1000, 0.035)

            # Hit-stop: brief slow-motion on impact (same idea as the rounds)
            if self.hitstop > 0:
                self.hitstop -= dt
                dt *= 0.12

            self.elapsed += dt
            self.fight_time += dt

            self.energy = min(100, self.energy + 8.5 * dt)
            for name in ("player_invuln", "dash_time", "attack_time", "attack_cooldown",
                         "special_time", "special_cooldown", "boss_stun", "player_hit",
                         "boss_hit", "shield_hit", "land_squash", "player_heal_cooldown",
                         "combo_timer", "combo_pulse"):
                setattr(self, name, max(0.0, getattr(self, name) - dt))
            self.boss_attack_timer -= dt
            if self.combo_timer <= 0:
                self.combo = 0
            self.g_attack_timer -= dt
            if self.g_attack_timer <= 0:
                self.g_attack_timer = self.rng.uniform(2.4, 3.5)

            # ------------------------------------------------
            # TWO-HAND WEBCAM CONTROLS
            # Left hand: horizontal position = movement; raise high = jump/dash.
            # Right hand: existing RandomForest gestures = attack/shield/heal/special.
            # ------------------------------------------------
            camera_state = None
            if self.camera_input_provider is not None:
                try:
                    camera_state = self.camera_input_provider()
                except Exception as exc:
                    self.gesture_status = "CAMERA ERROR"
                    print(f"Final Protocol camera input error: {exc}")
            self._update_hand_ui(camera_state)

            gesture_move = 0
            if camera_state and camera_state.get("camera_live"):
                self.camera_controls_live = True
                mx = camera_state.get("movement_x")
                my = camera_state.get("movement_y")
                if mx is not None:
                    self.gesture_move_x += (float(mx) - self.gesture_move_x) * min(1.0, 12.0 * dt)
                    if self.gesture_move_x < 0.43:
                        gesture_move = -1
                    elif self.gesture_move_x > 0.57:
                        gesture_move = 1
                gesture = camera_state.get("action_gesture") if camera_state.get("action_hand") else None
                self.blocking = bool(gesture == "shield") and self.charge_time <= 0
                self.handle_gesture_action(gesture)
                self.gesture_status = ("TWO HANDS ACTIVE" if camera_state.get("movement_hand") and camera_state.get("action_hand")
                                       else "SHOW BOTH HANDS")

                # Raise the movement hand above the upper quarter to dash; a moderate raise jumps.
                if my is not None and camera_state.get("movement_hand"):
                    if float(my) < 0.23 and not self.gesture_dash_latched and self.dash_time <= 0:
                        direction = gesture_move if gesture_move else self.player_facing
                        self.dash_dir = direction
                        self.player_facing = direction
                        self.dash_time = 0.22
                        self.player_invuln = max(self.player_invuln, 0.32)
                        self.last_player_action = self.elapsed
                        self.particle_burst(self.player_x, self.player_y - 20, BLUE_WHITE, 12, 160)
                        self.play("dash")
                        self.gesture_dash_latched = True
                    elif float(my) >= 0.30:
                        self.gesture_dash_latched = False
                    if 0.23 <= float(my) < 0.38 and self.player_y >= floor - 3 and self.player_vy == 0:
                        self.player_vy = -570
                        self.particle_burst(self.player_x, floor - 4, (150, 170, 195), 8, 130)
                        self.play("dash")
            else:
                self.camera_controls_live = False
                self.blocking = False
                self.gesture_status = "CAMERA OFFLINE - KEYBOARD BACKUP"
                self.last_camera_gesture = None

            # ------------------------------------------------
            # EVENTS (keyboard remains available as a fallback)
            # ------------------------------------------------

            for event in pygame.event.get():

                if event.type == pygame.QUIT:
                    self.stop_music()
                    self.exit_requested = True
                    return "defeat"

                if event.type != pygame.KEYDOWN:
                    continue

                if event.key == pygame.K_ESCAPE:
                    self.stop_music()
                    self.exit_requested = True
                    return "defeat"

                # J — normal attack (damage resolved on a fixed hit frame)
                if event.key == pygame.K_j and self.attack_cooldown <= 0 and self.dash_time <= 0:
                    if abs(self.boss_x - self.player_x) < 340:
                        self.player_facing = 1 if self.boss_x >= self.player_x else -1
                    self.attack_time = ATTACK_TIME
                    self.attack_cooldown = 0.38
                    self.attack_pending = True
                    self.last_player_action = self.elapsed
                    self.play("swing")

                # L — energy blast: charge, then release a projectile
                if (event.key == pygame.K_l and self.energy >= 35
                        and self.special_cooldown <= 0 and self.charge_time <= 0):
                    self.energy -= 35
                    self.charge_time = CHARGE_TIME
                    self.special_cooldown = 1.25
                    self.last_player_action = self.elapsed
                    self.play("charge")

                # SPACE — dash with afterimages
                if event.key == pygame.K_SPACE and self.dash_time <= 0:
                    keys = pygame.key.get_pressed()
                    direction = (int(keys[pygame.K_d] or keys[pygame.K_RIGHT])
                                 - int(keys[pygame.K_a] or keys[pygame.K_LEFT]))
                    if direction == 0:
                        direction = self.player_facing
                    self.dash_dir = direction
                    self.player_facing = direction
                    self.dash_time = 0.22
                    self.player_invuln = max(self.player_invuln, 0.32)
                    self.last_player_action = self.elapsed
                    self.particle_burst(self.player_x, self.player_y - 20, BLUE_WHITE, 12, 160)
                    self.play("dash")

            # ------------------------------------------------
            # PLAYER MOVEMENT / POSE
            # ------------------------------------------------

            keys = pygame.key.get_pressed()
            was_blocking = self.blocking
            if not self.camera_controls_live:
                self.blocking = bool(keys[pygame.K_k] or keys[pygame.K_DOWN]) and self.charge_time <= 0
            if self.blocking and not was_blocking:
                self.last_player_action = self.elapsed
                self.play("shield")
            self.shield_vis += ((1.0 if self.blocking else 0.0) - self.shield_vis) * min(1.0, 18 * dt)
            self.shield_vis = clamp(self.shield_vis, 0, 1)

            keyboard_move = (int(keys[pygame.K_d] or keys[pygame.K_RIGHT])
                             - int(keys[pygame.K_a] or keys[pygame.K_LEFT]))
            move = gesture_move if self.camera_controls_live else keyboard_move

            if self.dash_time > 0:
                self.player_x += self.dash_dir * 720 * dt
                self.ghost_timer -= dt
                if self.ghost_timer <= 0:
                    self.ghost_timer = 0.03
                    self.afterimages.append({"x": self.player_x, "y": self.player_y,
                                             "f": self.player_facing, "life": 0.28, "max": 0.28})
                    self.particles.append({"x": self.player_x - self.dash_dir * 30,
                                           "y": self.player_y - self.rng.randint(20, 220),
                                           "vx": -self.dash_dir * 60, "vy": 0, "life": 0.3,
                                           "max": 0.8, "size": 3, "color": BLUE_WHITE})
            elif move:
                self.player_facing = move
                speed = 320 * (0.3 if self.charge_time > 0 else 1.0) * (0.55 if self.blocking else 1.0)
                self.player_x += move * speed * dt

            # attack lunge
            if self.attack_time > 0.14:
                self.player_x += self.player_facing * 380 * dt

            # knockback from hits
            self.player_x += self.player_kb * dt
            self.player_kb *= max(0.0, 1 - 9 * dt)

            # attack hit frame (independent of animation visuals)
            if self.attack_pending and self.attack_time <= 0.16:
                self.attack_pending = False
                if abs(self.player_x - self.boss_x) < 220:
                    if self.damage_boss(9):
                        self.particle_burst(self.boss_x, self.boss_y - self.hand_h, BLUE, 10, 380)

            # charge -> release
            if self.charge_time > 0:
                self.charge_time -= dt
                self.ghost_timer = 0
                if self.charge_time <= 0:
                    self.special_time = 0.30
                    hx = self.player_x + self.player_facing * 70
                    hy = self.player_y - self.hand_h
                    self.projectiles.append({"x": hx, "y": hy, "vx": self.player_facing * 1150, "vy": 0,
                                             "owner": "gone", "damage": 18, "size": 26, "big": True})
                    self.add_impact(hx, hy, BLUE, 150, 0.3)
                    self.create_ring(hx, hy, BLUE_WHITE, 200)
                    self.particle_burst(hx, hy, BLUE_WHITE, 30, 380)
                    self.player_kb = -self.player_facing * 200
                    self.shake = max(self.shake, 5)
                    self.play("blast")
                else:
                    ang = self.rng.uniform(0, math.tau)
                    self.particles.append({"x": self.player_x + self.player_facing * 70 + math.cos(ang) * 90,
                                           "y": self.player_y - self.hand_h + math.sin(ang) * 90,
                                           "vx": -math.cos(ang) * 260, "vy": -math.sin(ang) * 260,
                                           "life": 0.3, "max": 0.8, "size": 3, "color": BLUE_WHITE})

            # jump + landing
            on_floor = abs(self.player_y - floor) < 3 and self.player_vy == 0
            if (not self.camera_controls_live and (keys[pygame.K_w] or keys[pygame.K_UP])) and on_floor:
                self.player_vy = -570
                self.particle_burst(self.player_x, floor - 4, (150, 170, 195), 8, 130)
                self.play("dash")

            self.player_vy += 1350 * dt
            self.player_y += self.player_vy * dt

            if self.player_y > floor:
                if self.was_airborne and self.player_vy > 250:
                    self.land_squash = 0.16
                    self.particle_burst(self.player_x, floor - 4, (150, 170, 195), 14, 170)
                    self.create_ring(self.player_x, floor - 4, BLUE, 80)
                    self.shake = max(self.shake, 2)
                    self.play("land")
                self.player_y = floor
                self.player_vy = 0
            self.was_airborne = self.player_y < floor - 3

            self.player_x = clamp(self.player_x, 80, self.width - 80)

            # ------------------------------------------------
            # RA.ONE (PPO policy) + effects
            # ------------------------------------------------

            self.boss_x += self.boss_kb * dt
            self.boss_kb *= max(0.0, 1 - 9 * dt)

            if self.boss_attack_time <= 0 and self.boss_stun <= 0:
                # RA.ONE actively hunts G.ONE instead of camping at the far edge.
                # Keep a short striking distance and approach from the nearest side.
                preferred_gap = 185
                if self.boss_x >= self.player_x:
                    target = self.player_x + preferred_gap
                else:
                    target = self.player_x - preferred_gap
                target = clamp(target, self.width * 0.18, self.width * 0.88)
                chase_speed = 245 if self.ppo_model is not None else 215
                self.boss_x += clamp(target - self.boss_x, -chase_speed * dt, chase_speed * dt)
            self.boss_x = clamp(self.boss_x, self.width * 0.12, self.width - 90)

            self.update_boss_ppo(dt)
            self.update_boss_attack(dt)
            self.update_projectiles(dt)
            self.update_effects(dt)
            self.update_fx(dt)

            # ------------------------------------------------
            # RENDER
            # ------------------------------------------------

            self.spawn_ambient_fx(dt)
            self.draw_background()

            # dash afterimages (G.ONE blue, RA.ONE red)
            for a in self.afterimages:
                k = a["life"] / a["max"]
                if a.get("boss"):
                    g = self.raone_ghost_flip
                else:
                    g = self.gone_ghost if a["f"] > 0 else self.gone_ghost_flip
                g.set_alpha(int((100 if a.get("boss") else 140) * k))
                self.screen.blit(g, (int(a["x"] - g.get_width() / 2), int(a["y"] - g.get_height())))
                g.set_alpha(255)

            # G.ONE pose
            f = self.player_facing
            rot, sx, sy, ox = 0.0, 1.0, 1.0, 0.0
            sy += 0.008 * math.sin(self.elapsed * 3.2)
            if self.attack_time > 0:
                p = 1 - self.attack_time / ATTACK_TIME
                rot = -f * (-8 * (p / 0.3) if p < 0.3 else 15 * math.sin(math.pi * (p - 0.3) / 0.7))
                sx, sy = 1.05, 0.98
            if self.dash_time > 0:
                rot, sx, sy = -f * 14, 1.14, 0.93
            if self.charge_time > 0:
                rot = f * 6
                sy = 0.97
            if self.special_time > 0:
                rot = -f * 10
            if self.player_vy < -50:
                sx, sy = 0.93, 1.08
            elif self.player_vy > 200 and self.player_y < floor - 3:
                sx, sy = 0.97, 1.04
            if self.land_squash > 0:
                k = self.land_squash / 0.16
                sx, sy = 1 + 0.14 * k, 1 - 0.12 * k
            if self.player_hit > 0:
                rot = f * 14 * self.player_hit / 0.3

            self.draw_character(
                self.gone_img, self.player_x, self.player_y, BLUE, f,
                130 if (self.player_invuln > 0 and self.dash_time <= 0 and int(self.elapsed * 20) % 2) else 255,
                1.0, rot=rot, stretch=(sx, sy),
                flash_img=self.gone_flash, flash=0.6 * self.player_hit / 0.3 if self.player_hit > 0 else 0,
            )

            # RA.ONE pose (telegraphs + hit reaction)
            brot, by_off, bsx, bsy = 0.0, 0.0, 1.0, 1.0
            if self.boss_attack == "slam" and self.boss_attack_time > 0.32:
                by_off = -70 * math.sin(math.pi * clamp((0.72 - self.boss_attack_time) / 0.4, 0, 1))
            elif self.boss_attack == "dash" and self.boss_attack_time < 0.48:
                brot, bsx = 14, 1.1
            elif self.boss_attack == "pulse" and self.boss_attack_time > 0:
                bsx = bsy = 1.0 + 0.05 * math.sin(self.elapsed * 30)
            if self.boss_cast_vis > 0:
                brot = -10 * math.sin(math.pi * (1 - self.boss_cast_vis / 0.35))
            if self.boss_hit > 0:
                brot = -(1 if self.boss_x >= self.player_x else -1) * 18 * self.boss_hit / 0.4
            self.draw_character(
                self.raone_img, self.boss_x, self.boss_y + by_off, RED, -1,
                125 if (self.boss_stun > 0 and self.boss_hit <= 0) else 255,
                1.02 if self.boss_phase == 2 else 1.0, rot=brot, stretch=(bsx, bsy),
                flash_img=self.raone_flash, flash=min(1.0, self.boss_hit / 0.2),
            )

            self.draw_effects()
            self.draw_fx()
            self.draw_boss_warning()
            self.draw_hud()
            self.draw_hand_controls_ui()

            # camera shake on the full frame
            if self.shake > 0:
                self._shake_buf.blit(self.screen, (0, 0))
                frame = self._shake_buf
                dx = self.rng.randint(-int(self.shake), int(self.shake))
                dy = self.rng.randint(-int(self.shake * 0.65), int(self.shake * 0.65))
                self.screen.fill((0, 0, 0))
                self.screen.blit(frame, (dx, dy))

            pygame.display.flip()

            # ------------------------------------------------
            # OUTCOME: decided by the first HP bar to reach 0
            # ------------------------------------------------

            if self.outcome is None:
                if self.gone_hp <= 0:
                    self.outcome = "defeat"
                elif self.raone_hp <= 0:
                    self.outcome = "victory"
                elif self.fight_time >= FIGHT_SECONDS:
                    self.outcome = "victory" if self.gone_hp > self.raone_hp else "defeat"
            if self.outcome:
                self.stop_music()
                return self.aftermath(self.outcome)


# ============================================================
# RUN AS A STANDALONE GAME FOR TESTING
# ============================================================

if __name__ == "__main__":

    pygame.init()

    try:
        pygame.mixer.init()
    except pygame.error:
        pass

    screen = pygame.display.set_mode(
        (1280, 720)
    )

    pygame.display.set_caption(
        "FINAL PROTOCOL — G.ONE vs RA.ONE"
    )

    clock = pygame.time.Clock()

    bonus = FinalProtocolBonus(
        screen,
        clock,
    )

    result = bonus.run()

    print("Bonus round result:", result)

    pygame.quit()