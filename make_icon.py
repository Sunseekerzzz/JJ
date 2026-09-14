# -*- coding: utf-8 -*-
"""生成应用图标 icon.ico(OBS 风格:深蓝底 + 白色胶片剪影)"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icon.ico")

SIZE = 256
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角深蓝背景
bg = (30, 110, 200, 255)
d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=52, fill=bg)

# 胶片剪影(白色):两条竖带 + 中间的取景框
white = (255, 255, 255, 255)
film_top, film_bottom = 44, 212
d.rounded_rectangle([56, film_top, 88, film_bottom], radius=8, fill=white)
d.rounded_rectangle([168, film_top, 200, film_bottom], radius=8, fill=white)

# 左/右竖带上的取景窗(镂空为背景色)
for x in (64, 80):
    for y in (64, 112, 160):
        d.rounded_rectangle([x, y, x + 16, y + 16], radius=3, fill=bg)

# 中间主窗口(深色取景框 + 白色边框)
d.rounded_rectangle([96, 68, 160, 188], radius=6, outline=white, width=6)
# 中间画面:播放三角(白色)
tri = [(118, 98), (118, 150), (150, 124)]
d.polygon(tri, fill=white)
# 画面底部信息条
d.rectangle([100, 164, 156, 184], fill=white)

# 存多尺寸 ico
img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                     (64, 64), (128, 128), (256, 256)])
print("icon saved:", OUT, os.path.getsize(OUT), "bytes")
