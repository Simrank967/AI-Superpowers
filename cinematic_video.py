"""Cinematic breach screen and ending-video playback for the bonus round."""
from pathlib import Path
import math
import random
import subprocess
import tempfile
import os

import cv2
import pygame


def show_hacked_screen(screen, clock, duration=4.2):
    """Show a red, electrical system breach. Returns False on quit/escape."""
    width, height = screen.get_size()
    elapsed = 0.0
    rng = random.Random()
    font = pygame.font.SysFont("monospace", max(25, min(42, width // 28)), bold=True)
    small = pygame.font.SysFont("monospace", max(14, min(20, width // 55)), bold=True)
    code_font = pygame.font.SysFont("monospace", 14)
    scanlines = pygame.Surface((width, height), pygame.SRCALPHA)
    for y in range(0, height, 4):
        pygame.draw.line(scanlines, (0, 0, 0, 38), (0, y), (width, y), 1)

    while elapsed < duration:
        dt = min(clock.tick(60) / 1000.0, 0.05)
        elapsed += dt
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False

        progress = elapsed / max(duration, 0.01)
        screen.fill((4, 0, 7))
        # Red emergency strobes with irregular blackout pulses.
        strobe = (math.sin(elapsed * 17) > 0.68 and rng.random() < 0.7)
        if strobe:
            screen.fill((48, 0, 9))
        if rng.random() < 0.035:
            screen.fill((0, 0, 0))

        # Corrupted terminal data running behind the main warning.
        for col in range(max(1, width // 155)):
            x = col * 155 + int(math.sin(elapsed * 2 + col) * 18)
            for row in range(8):
                y = int((row * 91 + elapsed * (110 + col * 9)) % height)
                code = ''.join(rng.choice("01X#%/\\<>") for _ in range(rng.randint(8, 18)))
                img = code_font.render(code, True, (100, rng.randint(0, 45), 30))
                img.set_alpha(rng.randint(35, 100))
                screen.blit(img, (x, y))

        # Jagged branching lightning bolts.
        bolt_layer = pygame.Surface((width, height), pygame.SRCALPHA)
        bolt_count = 1 + int(progress * 3)
        for _ in range(bolt_count):
            x = rng.randint(0, width)
            points = [(x, -5)]
            for _step in range(rng.randint(7, 13)):
                x += rng.randint(-65, 65)
                y = points[-1][1] + rng.randint(25, max(26, height // 11))
                points.append((max(0, min(width, x)), min(height, y)))
                if rng.random() < 0.22:
                    bx, by = points[-1]
                    branch = [(bx, by)]
                    for _b in range(3):
                        bx += rng.randint(-45, 45)
                        by += rng.randint(12, 35)
                        branch.append((max(0, min(width, bx)), min(height, by)))
                    pygame.draw.lines(bolt_layer, (255, 15, 45, 130), False, branch, 2)
            pygame.draw.lines(bolt_layer, (255, 0, 35, 55), False, points, 11)
            pygame.draw.lines(bolt_layer, (255, 20, 50, 190), False, points, 4)
            pygame.draw.lines(bolt_layer, (255, 225, 230, 235), False, points, 1)
        screen.blit(bolt_layer, (0, 0))

        # Horizontal corruption slices.
        for _ in range(5 if rng.random() < 0.75 else 15):
            y = rng.randrange(height)
            x = rng.randrange(max(1, width))
            w = rng.randint(20, max(21, width // 3))
            pygame.draw.rect(screen, rng.choice([(180, 0, 25), (55, 0, 14), (255, 30, 55)]),
                             (x, y, min(w, width - x), rng.choice([1, 2, 4])))

        title_text = "SYSTEM BREACHED"
        title = font.render(title_text, True, (255, 35, 60))
        title_rect = title.get_rect(center=(width // 2, height // 2 - 48))
        if rng.random() < 0.18:
            screen.blit(title, title_rect.move(rng.randint(-9, 9), rng.randint(-4, 4)))
        screen.blit(title, title_rect)
        messages = [
            "UNAUTHORIZED ACCESS DETECTED",
            "SECURITY PROTOCOLS DISABLED",
            "UNKNOWN ENTITY CONNECTED...",
            "RA.ONE CONTROL // 97.4%",
            "FINAL PROTOCOL INITIALIZING",
        ]
        message = messages[min(len(messages)-1, int(progress * len(messages)))]
        subtitle = small.render(message, True, (255, 180, 190))
        screen.blit(subtitle, subtitle.get_rect(center=(width // 2, height // 2 + 12)))

        # Progress bar and terminal footer.
        bar_w = int(width * 0.48)
        bar_x = (width - bar_w) // 2
        bar_y = height // 2 + 48
        pygame.draw.rect(screen, (75, 10, 24), (bar_x, bar_y, bar_w, 8), 1)
        pygame.draw.rect(screen, (255, 25, 55), (bar_x + 2, bar_y + 2, int((bar_w - 4) * progress), 4))
        footer = code_font.render("FATAL ERROR 0xRA-01  //  FIREWALL OVERRIDDEN", True, (170, 35, 55))
        screen.blit(footer, (max(10, width // 2 - footer.get_width() // 2), height - 34))
        screen.blit(scanlines, (0, 0))

        # Final blackout before handing off to the video.
        if elapsed > duration - 0.28:
            alpha = int(255 * min(1.0, (elapsed - (duration - 0.28)) / 0.28))
            blackout = pygame.Surface((width, height), pygame.SRCALPHA)
            blackout.fill((0, 0, 0, alpha))
            screen.blit(blackout, (0, 0))
        pygame.display.flip()
    return True


def play_ending_video(screen, clock, filename):
    """Play a local video with its original audio."""
    from shutil import which

    path = Path(filename)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path

    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")

    audio_file = None

    try:
        ffmpeg = which("ffmpeg")

        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                pass

        if ffmpeg:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp:
                audio_file = temp.name

            result = subprocess.run(
                [
                    ffmpeg, "-y", "-i", str(path),
                    "-map", "0:a:0", "-vn",
                    "-acodec", "pcm_s16le",
                    "-ar", "44100", "-ac", "2",
                    audio_file
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True
            )

            if result.returncode == 0 and os.path.getsize(audio_file) > 44:
                if not pygame.mixer.get_init():
                    pygame.mixer.init(
                        frequency=44100, size=-16,
                        channels=2, buffer=1024
                    )

                pygame.mixer.music.stop()
                pygame.mixer.music.load(audio_file)
                pygame.mixer.music.set_volume(1.0)
                pygame.mixer.music.play()
                print(f"VIDEO AUDIO ENABLED: {path.name}")
            else:
                print(f"VIDEO AUDIO EXTRACTION FAILED: {path.name}")
                print(result.stderr[-1500:])
        else:
            print("ERROR: FFmpeg not found.")

        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0 or fps > 120:
            fps = 30.0

        delay = 1.0 / fps
        accumulator = 0.0
        last_frame = None

        while True:
            dt = clock.tick(60) / 1000.0
            accumulator += dt

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    if pygame.mixer.get_init():
                        pygame.mixer.music.stop()
                    return False

                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    if pygame.mixer.get_init():
                        pygame.mixer.music.stop()
                    return False

            if accumulator >= delay or last_frame is None:
                ok, frame = cap.read()
                if not ok:
                    break
                last_frame = frame
                accumulator %= delay

            frame = cv2.cvtColor(last_frame, cv2.COLOR_BGR2RGB)
            frame_surface = pygame.image.frombuffer(
                frame.tobytes(), frame.shape[1::-1], "RGB"
            )

            sw, sh = screen.get_size()
            fw, fh = frame_surface.get_size()
            scale = min(sw / fw, sh / fh)

            frame_surface = pygame.transform.smoothscale(
                frame_surface,
                (int(fw * scale), int(fh * scale))
            )

            screen.fill((0, 0, 0))
            screen.blit(
                frame_surface,
                frame_surface.get_rect(center=(sw // 2, sh // 2))
            )
            pygame.display.flip()

        if pygame.mixer.get_init():
            pygame.mixer.music.stop()

        return True

    finally:
        cap.release()

        if audio_file and os.path.exists(audio_file):
            try:
                os.unlink(audio_file)
            except OSError:
                pass
