"""Voice2Docアプリアイコンを生成するワンオフスクリプト（オリジナル作画、ライセンス懸念なし）。

マイクのシンプルなフラットデザイン（第三者のアイコン素材は一切使用せず、図形の組み合わせで描画）:
- アプリのアクセントカラー(#e94560)を背景としたrounded square
- 白いマイク（カプセル型ヘッド・U字ホルダー・支柱・台座）

4倍の解像度で描画してから縮小し、輪郭を滑らかにする。

実行: python scripts/make_icon.py
出力: assets/icon.ico （16/32/48/256px）、assets/icon_preview.png
"""
import os

from PIL import Image, ImageDraw

PRIMARY = (108, 76, 241, 255)  # --primary (#6c4cf1)
WHITE = (255, 255, 255, 255)

SIZE = 256
SCALE = 4  # 作画解像度の倍率（縮小時のアンチエイリアス用）


def make_base_image() -> Image.Image:
    s = SIZE * SCALE
    u = s / 256  # 256基準の座標を作画解像度へ変換する係数

    def px(v: float) -> int:
        return round(v * u)

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景: rounded square
    margin = 8
    draw.rounded_rectangle(
        [px(margin), px(margin), px(SIZE - margin), px(SIZE - margin)],
        radius=px(56), fill=PRIMARY,
    )

    cx = 128
    # マイク本体（カプセル）
    cap_w, cap_top, cap_bottom = 64, 48, 140
    draw.rounded_rectangle(
        [px(cx - cap_w / 2), px(cap_top), px(cx + cap_w / 2), px(cap_bottom)],
        radius=px(cap_w / 2), fill=WHITE,
    )

    # U字ホルダー（下半円の弧）。カプセルとの間に隙間を空ける
    holder_r = 52
    holder_cy = 124
    stroke = 10
    draw.arc(
        [px(cx - holder_r), px(holder_cy - holder_r), px(cx + holder_r), px(holder_cy + holder_r)],
        start=0, end=180, fill=WHITE, width=px(stroke),
    )

    # 支柱と台座
    stem_top = holder_cy + holder_r - 2
    stem_bottom = 202
    draw.rectangle(
        [px(cx - stroke / 2), px(stem_top), px(cx + stroke / 2), px(stem_bottom)],
        fill=WHITE,
    )
    base_w, base_h = 72, 11
    draw.rounded_rectangle(
        [px(cx - base_w / 2), px(stem_bottom - 2), px(cx + base_w / 2), px(stem_bottom - 2 + base_h)],
        radius=px(base_h / 2), fill=WHITE,
    )

    return img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main():
    img = make_base_image()
    out_dir = "assets"
    os.makedirs(out_dir, exist_ok=True)
    ico_path = os.path.join(out_dir, "icon.ico")
    img.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    print(f"Wrote {ico_path}")

    # 確認用PNGも残す
    png_path = os.path.join(out_dir, "icon_preview.png")
    img.save(png_path, format="PNG")
    print(f"Wrote {png_path}")


if __name__ == "__main__":
    main()
