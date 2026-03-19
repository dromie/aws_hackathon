import pygame
import requests
import math
import random
import io
from PIL import Image

# --- Térkép konfiguráció (Budapest, Hősök tere) ---
CENTER_LAT, CENTER_LON = 47.5140, 18.5760
RALLY_LAT,  RALLY_LON  = 47.5149, 18.5763
ZOOM   = 15
TILES  = 3
TILE_SIZE = 256
MAP_W = MAP_H = TILE_SIZE * TILES
FPS   = 30
WANDER_FRAMES = FPS * 6   # 6 másodperc bolyongás


def latlon_to_tile_float(lat, lon, zoom):
    n = 2 ** zoom
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def download_map():
    cx, cy = latlon_to_tile_float(CENTER_LAT, CENTER_LON, ZOOM)
    tx0 = int(cx) - TILES // 2
    ty0 = int(cy) - TILES // 2
    img = Image.new("RGB", (MAP_W, MAP_H))
    headers = {"User-Agent": "CrowdMapDemo/1.0"}
    for dy in range(TILES):
        for dx in range(TILES):
            url = f"https://tile.openstreetmap.org/{ZOOM}/{tx0+dx}/{ty0+dy}.png"
            try:
                r = requests.get(url, headers=headers, timeout=6)
                tile = Image.open(io.BytesIO(r.content)).convert("RGB")
                img.paste(tile, (dx * TILE_SIZE, dy * TILE_SIZE))
            except Exception as e:
                print(f"Tile hiba: {e}")
    return img, tx0, ty0


def latlon_to_px(lat, lon, tx0, ty0):
    tx, ty = latlon_to_tile_float(lat, lon, ZOOM)
    return (tx - tx0) * TILE_SIZE, (ty - ty0) * TILE_SIZE


def pil_to_pygame(img):
    return pygame.image.fromstring(img.tobytes(), img.size, "RGB")


# --- Csoport ---
class Group:
    def __init__(self, x, y, count):
        self.x, self.y = float(x), float(y)
        self.count = count
        self.vx = random.uniform(-1.5, 1.5)
        self.vy = random.uniform(-1.5, 1.5)
        self.alive = True

    @property
    def radius(self):
        return max(8, 6 + self.count * 1.4)

    def color(self):
        t = min(self.count / 50, 1.0)
        return (int(30 + 225 * t), int(180 * (1 - t)), int(220 * (1 - t)))


def main():
    pygame.init()
    screen = pygame.display.set_mode((MAP_W, MAP_H + 36))
    pygame.display.set_caption("Tömeg szimuláció – Budapest")
    font = pygame.font.SysFont("Arial", 13, bold=True)
    clock = pygame.time.Clock()

    # Térkép letöltés
    screen.fill((30, 30, 30))
    msg = font.render("Térkép letöltése...", True, (255, 255, 255))
    screen.blit(msg, (MAP_W // 2 - msg.get_width() // 2, MAP_H // 2))
    pygame.display.flip()

    map_img, tx0, ty0 = download_map()
    map_surf = pil_to_pygame(map_img)

    rally_x, rally_y = latlon_to_px(RALLY_LAT, RALLY_LON, tx0, ty0)

    # Csoportok létrehozása
    groups = [Group(random.randint(30, MAP_W - 30),
                    random.randint(30, MAP_H - 30),
                    random.randint(3, 5)) for _ in range(12)]

    frame = 0
    phase = "wander"

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        frame += 1
        if frame == WANDER_FRAMES:
            phase = "rally"

        alive = [g for g in groups if g.alive]

        # Mozgás
        for g in alive:
            if phase == "wander":
                if random.random() < 0.04:
                    g.vx = random.uniform(-1.5, 1.5)
                    g.vy = random.uniform(-1.5, 1.5)
                g.x = max(10, min(MAP_W - 10, g.x + g.vx))
                g.y = max(10, min(MAP_H - 10, g.y + g.vy))
                if g.x <= 10 or g.x >= MAP_W - 10: g.vx *= -1
                if g.y <= 10 or g.y >= MAP_H - 10: g.vy *= -1
            else:
                dx, dy = rally_x - g.x, rally_y - g.y
                dist = math.hypot(dx, dy)
                if dist > 2:
                    speed = min(2.0 + g.count * 0.04, 4.5)
                    g.x += dx / dist * speed
                    g.y += dy / dist * speed

        # Összeolvadás
        alive = [g for g in groups if g.alive]
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                a, b = alive[i], alive[j]
                if not b.alive: continue
                if math.hypot(a.x - b.x, a.y - b.y) < a.radius + b.radius - 4:
                    a.count += b.count
                    b.alive = False

        # Rajzolás
        screen.blit(map_surf, (0, 0))

        # Rally jelölő
        rx, ry = int(rally_x), int(rally_y)
        pygame.draw.circle(screen, (220, 30, 30), (rx, ry), 10, 2)
        pygame.draw.line(screen, (220, 30, 30), (rx - 14, ry), (rx + 14, ry), 1)
        pygame.draw.line(screen, (220, 30, 30), (rx, ry - 14), (rx, ry + 14), 1)

        for g in groups:
            if not g.alive: continue
            r = int(g.radius)
            cx, cy = int(g.x), int(g.y)
            # Átlátszó kör: külön surface
            surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            c = g.color()
            pygame.draw.circle(surf, (*c, 160), (r, r), r)
            pygame.draw.circle(surf, (255, 255, 255, 200), (r, r), r, 1)
            screen.blit(surf, (cx - r, cy - r))
            lbl = font.render(str(g.count), True, (255, 255, 255))
            screen.blit(lbl, (cx - lbl.get_width() // 2, cy - lbl.get_height() // 2))

        # Státusz sáv
        pygame.draw.rect(screen, (30, 30, 30), (0, MAP_H, MAP_W, 36))
        phase_txt = "🚶 Bolyongás..." if phase == "wander" else "🏃 Gyülekezés → Hősök tere"
        alive_count = sum(g.count for g in groups if g.alive)
        status = font.render(f"{phase_txt}   |   Emberek: {alive_count}   |   ESC: kilépés", True, (200, 200, 200))
        screen.blit(status, (10, MAP_H + 9))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    main()
