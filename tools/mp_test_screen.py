# Test screen for Waveshare ESP32-C6-LCD-1.47 (ST7789, 172x320 IPS)
import time, framebuf, network, ubinascii
from machine import Pin, SPI

W, H = 172, 320
X_OFF, Y_OFF = 34, 0  # 172-px panel centered on the ST7789's 240-px column RAM

spi = SPI(1, baudrate=40_000_000, polarity=0, phase=0, sck=Pin(7), mosi=Pin(6))
cs = Pin(14, Pin.OUT, value=1)
dc = Pin(15, Pin.OUT, value=0)
rst = Pin(21, Pin.OUT, value=1)
bl = Pin(22, Pin.OUT, value=0)


def cmd(c, data=None):
    cs(0)
    dc(0)
    spi.write(bytes([c]))
    if data is not None:
        dc(1)
        spi.write(data)
    cs(1)


def init():
    rst(0); time.sleep_ms(20); rst(1); time.sleep_ms(150)
    cmd(0x01); time.sleep_ms(150)          # SWRESET
    cmd(0x11); time.sleep_ms(120)          # SLPOUT
    cmd(0x3A, b"\x55")                     # COLMOD: 16-bit RGB565
    cmd(0x36, b"\x00")                     # MADCTL: portrait, RGB order
    cmd(0x21)                              # INVON (IPS panel)
    cmd(0x13)                              # NORON
    cmd(0x29); time.sleep_ms(20)           # DISPON


def blit(buf):
    x0, x1 = X_OFF, X_OFF + W - 1
    y0, y1 = Y_OFF, Y_OFF + H - 1
    cmd(0x2A, bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
    cmd(0x2B, bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
    cmd(0x2C, buf)


def rgb(r, g, b):
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return ((c & 0xFF) << 8) | (c >> 8)  # framebuf is little-endian, panel wants big-endian


buf = bytearray(W * H * 2)
fb = framebuf.FrameBuffer(buf, W, H, framebuf.RGB565)

WHITE, BLACK = rgb(255, 255, 255), rgb(0, 0, 0)
fb.fill(BLACK)

# SMPTE-style colour bars
bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
        (255, 0, 255), (255, 0, 0), (0, 0, 255)]
for i, c in enumerate(bars):
    x0 = i * W // len(bars)
    x1 = (i + 1) * W // len(bars)
    fb.fill_rect(x0, 0, x1 - x0, 110, rgb(*c))

# 16-step grey ramp
for i in range(16):
    v = i * 255 // 15
    x0 = i * W // 16
    fb.fill_rect(x0, 112, (i + 1) * W // 16 - x0, 24, rgb(v, v, v))

# Smooth R, G, B gradients
for x in range(W):
    v = x * 255 // (W - 1)
    fb.vline(x, 140, 14, rgb(v, 0, 0))
    fb.vline(x, 156, 14, rgb(0, v, 0))
    fb.vline(x, 172, 14, rgb(0, 0, v))

# Labels
mac = ubinascii.hexlify(network.WLAN(network.STA_IF).config("mac"), ":").decode()
lines = [("ESP32-C6-LCD-1.47", WHITE), ("ST7789  172x320", rgb(0, 255, 255)),
         ("MicroPython", rgb(255, 255, 0)), (mac[:8], rgb(180, 180, 180)),
         (mac[9:], rgb(180, 180, 180))]
for i, (t, c) in enumerate(lines):
    fb.text(t, (W - len(t) * 8) // 2, 196 + i * 14, c)

# Border + corner markers to confirm the panel offset is right
fb.rect(0, 0, W, H, WHITE)
for x, y in ((0, 0), (W - 6, 0), (0, H - 6), (W - 6, H - 6)):
    fb.fill_rect(x, y, 6, 6, rgb(255, 0, 0))

init()
blit(buf)
bl(1)
print("test screen drawn")
