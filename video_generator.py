#!/usr/bin/env python3
"""
video_generator.py
カロリっち TikTok / Instagram Reels 用動画を自動生成するスクリプト。

【出力】
  SNS/videos/calopet_tiktok.mp4     縦型 1080×1920（TikTok / Reels）
  SNS/videos/calopet_square.mp4     正方形 1080×1080（Instagram Feed）

【実行】
  python3 video_generator.py                # 両方生成
  python3 video_generator.py tiktok         # 縦型のみ
  python3 video_generator.py square         # 正方形のみ
  python3 video_generator.py voiceover      # ElevenLabs 音声付き縦型

【ElevenLabs ボイスオーバー（任意）】
  ELEVENLABS_API_KEY 環境変数を設定して "voiceover" モードで実行
  export ELEVENLABS_API_KEY="your_key_here"

【Reels 仕様】
  - 解像度: 1080×1920 (9:16) / 1080×1080 (1:1)
  - 長さ: 30秒以内
  - コーデック: H.264 / AAC
  - fps: 30
"""

import os
import sys
import json
import urllib.request
import urllib.error
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
from moviepy import (
    ImageClip, VideoClip, CompositeVideoClip,
    AudioFileClip, concatenate_videoclips, ColorClip
)
from moviepy.video.fx import CrossFadeIn, CrossFadeOut

# ============================================================
# パス定義
# ============================================================
BASE_DIR   = Path(__file__).parent
IMG_DIR    = BASE_DIR / "images"
VIDEO_DIR  = BASE_DIR / "videos"
AUDIO_DIR  = BASE_DIR / "audio"
# bgm_main.m4a / bgm_main.mp3 / bgm_main.wav のいずれかを自動検出
def _find_bgm():
    for ext in ["m4a", "mp3", "wav", "aac"]:
        p = AUDIO_DIR / f"bgm_main.{ext}"
        if p.exists():
            return p
    return None

BGM_PATH = _find_bgm()  # 起動時に解決（ファイルがなければ None）

# Instagram Reels セーフゾーン（公式仕様）
# 上部108px以上、下部300〜320px（いいね/コメント/保存ボタン干渉防止）
SAFE_MARGIN_TOP    = 150
SAFE_MARGIN_BOTTOM = 320
SAFE_MARGIN_SIDE   = 80

VIDEO_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

# フォントフォールバック（macOS / Linux / 同梱フォント の順で試行）
_FONT_CANDIDATES_BOLD = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",      # macOS Bold
    "/System/Library/Fonts/Hiragino Sans GB W6.ttc",         # macOS代替
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",   # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf", # Linux代替
    str(BASE_DIR / "fonts" / "NotoSansJP-Bold.ttf"),          # 同梱フォント
]
_FONT_CANDIDATES_MED = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",       # macOS Medium
    "/System/Library/Fonts/Hiragino Sans GB W3.ttc",          # macOS代替
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Medium.ttc",  # Linux
    str(BASE_DIR / "fonts" / "NotoSansJP-Medium.ttf"),         # 同梱フォント
]

# ElevenLabs
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"   # Adam（日本語対応）→ 後で差し替え可

# ============================================================
# スライド定義（各スライドの内容・時間）
# 【バズ最適化版】合計15秒 / フック+ループ構造
#   0〜 2s: フック   「ダイエット続かない人必見」（冒頭0.5秒で離脱防止）
#   2〜 5s: スライド1 アプリ概念
#   5〜 9s: スライド2 記録結果（中盤に「保存して！」テキスト）
#   9〜12s: スライド3 ガチャ演出（EX確定）
#  12〜15s: CTA        「今すぐ無料DL」→ 最後のフレームを冒頭に繋げるループ設計
# ============================================================
SLIDES = [
    {
        "image":    None,
        "duration": 2,
        "_type":    "hook",   # ← 冒頭2秒フック：make_hook_image() で生成
    },
    {
        "image":    IMG_DIR / "calopet_share_01.png",
        "duration": 3,
        # カロリっちの正しいコンセプト：食べなかった分（制限した分）だけペットが育つ
        "caption":  "カロリーを制限するほど\nペットが育つ",
        "caption_pos": "bottom",
    },
    {
        "image":    IMG_DIR / "calopet_share_02.png",
        "duration": 4,
        "caption":  "記録するだけで変わった",
        "caption_pos": "bottom",
        "_save_overlay": True,  # ← 中盤に「後で見返したい人は保存して！」テキスト追加
    },
    {
        "image":    IMG_DIR / "calopet_share_03.png",
        "duration": 3,
        "caption":  "EX確定！\nがんばったご褒美",
        "caption_pos": "bottom",
    },
    {
        "image":    None,   # CTA スライド（画像なし・生成）
        "duration": 3,
        # ⚠️ caption は空にする：make_cta_image が直接テキストを描画するため
        "bg_color": (30, 10, 60),
    },
]

# ボイスオーバーのテキスト（ElevenLabs 用）
VOICEOVER_SCRIPT = (
    # ⚠️ カロリっちのコンセプト：「食べて増やす」ではなく「食べなかった分（制限した分）だけペットが育つ」
    "カロリっちは、カロリーを抑えるほどペットが育つゲームアプリです。"
    "制限が栄養になる。がまんがペットへの愛に変わる、新感覚ダイエットアプリ。"
    "今すぐApp Storeで無料ダウンロードしてみてください。"
)

# ============================================================
# ユーティリティ
# ============================================================

def pil_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """指定パスのフォントを読み込む。存在しない場合はフォールバックを試みる。"""
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    # フォールバックチェーンを試行
    for candidate in _FONT_CANDIDATES_BOLD:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    print(f"[WARN] フォントが見つかりません: {path} → デフォルトフォントを使用")
    return ImageFont.load_default()

def get_font_bold(size: int) -> ImageFont.FreeTypeFont:
    """太字フォント（ふりがな・テロップ用）"""
    for candidate in _FONT_CANDIDATES_BOLD:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()

def get_font_med(size: int) -> ImageFont.FreeTypeFont:
    """中字フォント（サブテキスト用）"""
    for candidate in _FONT_CANDIDATES_MED:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return get_font_bold(size)

import re as _re

def _strip_emoji(text: str) -> str:
    """PILフォントが描画できない絵文字・特殊文字を除去する"""
    # BMP外の文字（U+10000以上）と代表的な記号絵文字を除去
    pattern = _re.compile(
        "["
        "\U0001F300-\U0001F9FF"   # 絵文字全般
        "\U00002702-\U000027B0"   # Dingbats
        "\U000024C2-\U0001F251"   # その他記号
        "\U0001FA00-\U0001FA6F"   # 追加絵文字
        "\U0001FA70-\U0001FAFF"
        "\U00002500-\U00002BEF"   # その他記号
        "]+",
        flags=_re.UNICODE
    )
    return pattern.sub("", text).strip()


def make_text_image(text: str, width: int, font_path: str, font_size: int,
                    color=(255,255,255), bg=(0,0,0,0), padding=20,
                    stroke_width=3, stroke_color=(0,0,0),
                    pill_bg=True) -> Image.Image:
    """
    テキストを中央揃えで描画した RGBA 画像を返す。
    pill_bg=True のとき、テキスト背景に半透明の暗い角丸ボックスを描画して視認性を上げる。
    絵文字はPILフォント非対応のため自動除去。
    """
    text = _strip_emoji(text)
    if not text:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    fnt = pil_font(font_path, font_size)
    lines = text.split("\n")

    tmp = Image.new("RGBA", (1, 1))
    d   = ImageDraw.Draw(tmp)
    line_heights = [d.textbbox((0,0), l, font=fnt)[3] - d.textbbox((0,0), l, font=fnt)[1]
                    for l in lines]
    max_w = max(d.textlength(l, font=fnt) for l in lines)
    line_gap = 14
    total_h = sum(line_heights) + line_gap * (len(lines) - 1)

    img_w = int(max_w) + padding * 2
    img_h = int(total_h) + padding * 2
    img   = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)

    # 半透明の角丸背景ピル（視認性確保）
    if pill_bg:
        pill = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        pill_draw = ImageDraw.Draw(pill)
        pill_draw.rounded_rectangle(
            [0, 0, img_w - 1, img_h - 1],
            radius=20,
            fill=(0, 0, 0, 160)   # 黒63%透明
        )
        img = Image.alpha_composite(img, pill)
        draw = ImageDraw.Draw(img)

    y = padding
    for i, (line, lh) in enumerate(zip(lines, line_heights)):
        lw = int(draw.textlength(line, font=fnt))
        x  = (img_w - lw) // 2
        # ストローク（縁取り）
        for dx, dy in [(-stroke_width,0),(stroke_width,0),(0,-stroke_width),(0,stroke_width),
                       (-stroke_width,-stroke_width),(stroke_width,-stroke_width),
                       (-stroke_width,stroke_width),(stroke_width,stroke_width)]:
            draw.text((x+dx, y+dy), line, font=fnt, fill=stroke_color)
        draw.text((x, y), line, font=fnt, fill=color)
        y += lh + line_gap

    return img

def make_hook_image(width: int, height: int) -> Image.Image:
    """
    冒頭フックスライドの背景画像を生成（2秒間表示）。
    「ダイエット続かない人必見」を大きく表示して離脱を防ぐ。
    """
    import random
    img  = Image.new("RGBA", (width, height), (8, 4, 20, 255))
    draw = ImageDraw.Draw(img)

    # 星（宇宙感・ドット絵テイスト）
    rng = random.Random(99)
    for _ in range(140):
        x, y = rng.randint(0, width), rng.randint(0, height)
        r    = rng.choice([1, 2, 3])
        draw.ellipse([x-r, y-r, x+r, y+r], fill=(255, 255, 255, rng.randint(40, 130)))

    # 赤いアイキャッチバー（視線を引きつける）
    bar_y  = height // 2 - 130
    bar_h  = 240
    draw.rectangle([0, bar_y, width, bar_y + bar_h], fill=(140, 18, 55, 220))

    # メインテキスト（2行）
    fnt_lg = get_font_bold(84)
    lines  = ["ダイエット", "続かない人必見"]
    y_cur  = bar_y + 18
    for line in lines:
        w = int(draw.textlength(line, font=fnt_lg))
        x = (width - w) // 2
        # 黒縁取り
        for dx, dy in [(-3,0),(3,0),(0,-3),(0,3),(-3,-3),(3,-3),(-3,3),(3,3)]:
            draw.text((x+dx, y_cur+dy), line, font=fnt_lg, fill=(0, 0, 0, 255))
        draw.text((x, y_cur), line, font=fnt_lg, fill=(255, 240, 80, 255))
        y_cur += int(draw.textbbox((0,0), line, font=fnt_lg)[3]) + 8

    # サブテキスト（コンセプト）
    fnt_sm = get_font_med(46)
    sub    = "カロリーを制限するほどペットが育つ"
    sub_w  = int(draw.textlength(sub, font=fnt_sm))
    x      = (width - sub_w) // 2
    draw.text((x, height // 2 + 130), sub, font=fnt_sm, fill=(220, 200, 255, 225))

    return img


def make_cta_image(width: int, height: int) -> Image.Image:
    """CTA スライドの背景画像を Pillow で生成"""
    from PIL import ImageDraw
    img = Image.new("RGBA", (width, height), (20, 8, 45, 255))
    draw = ImageDraw.Draw(img)

    # 星
    import random
    rng = random.Random(42)
    for _ in range(150):
        x, y = rng.randint(0, width), rng.randint(0, height)
        r = rng.choice([2, 3, 4])
        draw.ellipse([x-r, y-r, x+r, y+r], fill=(255,255,255,rng.randint(60,150)))

    # アプリアイコン
    icon_path = (BASE_DIR / "../CaloPet/Resources/Assets.xcassets"
                 "/AppIcon.appiconset/icon_1024.png").resolve()
    if icon_path.exists():
        icon = Image.open(icon_path).convert("RGBA").resize((260, 260), Image.LANCZOS)
        mask = Image.new("L", (260, 260), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0,0,259,259], radius=52, fill=255)
        icon.putalpha(mask)
        ix = (width - 260) // 2
        iy = height // 2 - 220
        img.paste(icon, (ix, iy), icon)

    # アプリ名
    fnt = get_font_bold(90)
    name = "カロリっち"
    draw.text(((width - int(draw.textlength(name, font=fnt))) // 2,
               height // 2 + 60), name, font=fnt, fill=(255,255,255,255))

    # URL
    fnt_sm = get_font_med(36)
    url = "App Store で無料ダウンロード"
    draw.text(((width - int(draw.textlength(url, font=fnt_sm))) // 2,
               height // 2 + 170), url, font=fnt_sm, fill=(200, 180, 255, 220))

    return img

def numpy_from_pil(pil_img: Image.Image) -> np.ndarray:
    return np.array(pil_img.convert("RGB"))

# ============================================================
# ElevenLabs ボイスオーバー生成
# ============================================================

def generate_voiceover(text: str, out_path: Path) -> bool:
    """ElevenLabs API で音声ファイルを生成する（mp3）。失敗時は False を返す。"""
    if not ELEVENLABS_API_KEY:
        print("[WARN] ELEVENLABS_API_KEY 未設定 → 音声なしで生成します")
        return False

    import json, urllib.request
    url     = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    payload = json.dumps({
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            out_path.write_bytes(resp.read())
            print(f"  ✅ ボイスオーバー生成: {out_path}")
            return True
    except urllib.error.HTTPError as e:
        print(f"  [ERROR] ElevenLabs: {e.code} {e.read().decode()}")
        return False

# ============================================================
# 動画生成コア
# ============================================================

def fit_image_to_canvas(pil_img: Image.Image, canvas_w: int, canvas_h: int) -> Image.Image:
    """アスペクト比を保ちつつキャンバスにフィット（中央クロップ）"""
    bg_ratio = pil_img.width / pil_img.height
    cv_ratio = canvas_w / canvas_h
    if bg_ratio > cv_ratio:
        new_h = canvas_h
        new_w = int(canvas_h * bg_ratio)
    else:
        new_w = canvas_w
        new_h = int(canvas_w / bg_ratio)
    pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - canvas_w) // 2
    top  = (new_h - canvas_h) // 2
    return pil_img.crop((left, top, left + canvas_w, top + canvas_h))


def fit_image_portrait(pil_img: Image.Image, canvas_w: int, canvas_h: int) -> Image.Image:
    """
    縦型キャンバス(1080×1920)への最適フィット。
    正方形・横長・縦長のいずれの画像でも「ブラー背景＋前景センタリング」で
    自然に埋める。Instagram Reels / TikTok に最適化。

    処理フロー:
    1. 背景: 元画像をキャンバスサイズに引き伸ばして強ブラー（ぼかし背景）
    2. 前景: アスペクト比保持でキャンバス幅/高さの 85% 以内にフィット
    3. 前景を上寄りに配置（テロップ用の下部スペースを確保）
    """
    canvas = Image.new("RGB", (canvas_w, canvas_h), (10, 5, 20))

    # 1. ブラー背景（キャンバス全体を覆うようにリサイズ → 強ブラー）
    bg = pil_img.copy().convert("RGB")
    bg = bg.resize((canvas_w, canvas_h), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
    # 暗め暗転（ブランドカラーに合わせる）
    overlay = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
    bg = Image.blend(bg, overlay, alpha=0.45)
    canvas.paste(bg)

    # 2. 前景: アスペクト比保持でキャンバスの 85% 幅 or 75% 高さ に収まるよう縮小
    max_fg_w = int(canvas_w * 0.85)
    max_fg_h = int(canvas_h * 0.72)  # 下部に 28% のテロップ余白を確保
    fg = pil_img.convert("RGB")
    fg.thumbnail((max_fg_w, max_fg_h), Image.LANCZOS)

    # 3. 前景をキャンバス上部寄り中央に配置（上から SAFE_MARGIN_TOP + 20px 余白）
    fg_x = (canvas_w - fg.width) // 2
    fg_y = SAFE_MARGIN_TOP + 20
    canvas.paste(fg, (fg_x, fg_y))

    return canvas

def build_clip(slide: dict, canvas_w: int, canvas_h: int) -> VideoClip:
    """1スライド分の VideoClip を生成（Ken Burns ズーム + キャプション付き）"""
    duration = slide["duration"]

    # 背景の準備
    # 縦型キャンバス(1080×1920)では、画像がぴったり1080×1920でない限り
    # 必ずブラー背景フィットを使う（クロップで端が切れるのを防ぐ）
    is_portrait_canvas = canvas_h > canvas_w
    if slide.get("_type") == "hook":
        # 冒頭フックスライド
        pil_bg = make_hook_image(canvas_w, canvas_h).convert("RGB")
    elif slide.get("image") and slide["image"].exists():
        src = Image.open(slide["image"]).convert("RGB")
        if is_portrait_canvas and (src.width, src.height) != (canvas_w, canvas_h):
            # サイズが違う全ての画像 → ブラー背景フィット（切り抜きゼロ）
            pil_bg = fit_image_portrait(src, canvas_w, canvas_h)
        else:
            pil_bg = fit_image_to_canvas(src, canvas_w, canvas_h)
    elif slide.get("bg_color"):
        pil_bg = make_cta_image(canvas_w, canvas_h).convert("RGB")
    else:
        pil_bg = Image.new("RGB", (canvas_w, canvas_h), (20, 8, 45))

    bg_arr = np.array(pil_bg)

    # キャプション画像を事前生成
    caption  = slide.get("caption", "")
    cap_data = None
    if caption:
        cap_img = make_text_image(
            caption, canvas_w,
            font_path=_FONT_CANDIDATES_BOLD[0], font_size=58,
            color=(255,255,255), bg=(0,0,0,0),
            padding=28, stroke_width=3, stroke_color=(0,0,0),
            pill_bg=True
        )
        cap_rgb = np.array(cap_img.convert("RGB"))
        cap_a   = np.array(cap_img.split()[-1], dtype=float) / 255.0

        pos_name = slide.get("caption_pos", "bottom")
        if pos_name == "bottom":
            # ⚠️ Instagram Reels セーフゾーン：下部ボタン(いいね/保存/コメント)との干渉防止
            cap_y = canvas_h - cap_img.height - SAFE_MARGIN_BOTTOM
        elif pos_name == "top":
            # ⚠️ 上部セーフゾーン：プロフィール情報との干渉防止
            cap_y = SAFE_MARGIN_TOP
        else:
            cap_y = (canvas_h - cap_img.height) // 2
        # 左右センタリング（サイドマージン内に収める）
        cap_x = max(SAFE_MARGIN_SIDE, (canvas_w - cap_img.width) // 2)
        cap_data = (cap_rgb, cap_a, cap_x, cap_y, cap_img.height, cap_img.width)

    # 「保存して！」保存促進オーバーレイ（_save_overlay: True のスライドにのみ表示）
    # → 中盤 1.5 秒後から 0.5 秒でフェードイン → 終端 0.5 秒でフェードアウト
    save_data = None
    if slide.get("_save_overlay"):
        save_text = "後で見返したい人は保存して！"
        save_img  = make_text_image(
            save_text, canvas_w,
            font_path=_FONT_CANDIDATES_BOLD[0], font_size=52,
            color=(255, 240, 80),  # 黄色で目立たせる
            bg=(0,0,0,0),
            padding=24, stroke_width=3, stroke_color=(0,0,0),
            pill_bg=True
        )
        save_rgb = np.array(save_img.convert("RGB"))
        save_a   = np.array(save_img.split()[-1], dtype=float) / 255.0
        # 上部セーフゾーン直下に配置（プロフィール行との干渉を避ける）
        save_y = SAFE_MARGIN_TOP + 20
        save_x = max(SAFE_MARGIN_SIDE, (canvas_w - save_img.width) // 2)
        save_data = (save_rgb, save_a, save_x, save_y, save_img.height, save_img.width)

    # Ken Burns ズームエフェクト + キャプション合成を per-frame 関数で実装
    zoom_start, zoom_end = 1.0, 1.05

    def make_frame(t):
        # ズーム
        progress = t / duration
        scale = zoom_start + (zoom_end - zoom_start) * progress
        new_w = int(canvas_w * scale)
        new_h = int(canvas_h * scale)
        frame = np.array(
            Image.fromarray(bg_arr).resize((new_w, new_h), Image.BILINEAR)
        )
        ox = (new_w - canvas_w) // 2
        oy = (new_h - canvas_h) // 2
        frame = frame[oy:oy+canvas_h, ox:ox+canvas_w].copy()

        # キャプション合成
        if cap_data:
            cap_rgb, cap_a, cx, cy, ch, cw = cap_data
            alpha_t = float(min(1.0, t * 2))
            roi = frame[cy:cy+ch, cx:cx+cw].astype(float)
            for c in range(3):
                roi[:, :, c] = roi[:, :, c] * (1 - cap_a * alpha_t) + cap_rgb[:, :, c] * cap_a * alpha_t
            frame[cy:cy+ch, cx:cx+cw] = roi.clip(0, 255).astype(np.uint8)

        # 保存促進テキスト合成（1.5〜終端-0.5 秒の間でフェードイン/アウト）
        if save_data:
            fade_in_start  = 1.5
            fade_in_end    = 2.0
            fade_out_start = duration - 0.5
            fade_out_end   = duration
            if t >= fade_in_start:
                if t < fade_in_end:
                    alpha_t = (t - fade_in_start) / (fade_in_end - fade_in_start)
                elif t < fade_out_start:
                    alpha_t = 1.0
                else:
                    alpha_t = max(0.0, (fade_out_end - t) / (fade_out_end - fade_out_start))
                alpha_t = float(alpha_t)
                s_rgb, s_a, sx, sy, sh, sw = save_data
                roi = frame[sy:sy+sh, sx:sx+sw].astype(float)
                for c in range(3):
                    roi[:, :, c] = roi[:, :, c] * (1 - s_a * alpha_t) + s_rgb[:, :, c] * s_a * alpha_t
                frame[sy:sy+sh, sx:sx+sw] = roi.clip(0, 255).astype(np.uint8)

        return frame

    clip = VideoClip(make_frame, duration=duration)
    clip = clip.with_effects([CrossFadeIn(0.5), CrossFadeOut(0.5)])
    return clip

def validate_video(path: Path) -> list[str]:
    """
    投稿前バリデーション。Instagram Reels の仕様違反を事前検出する。
    問題があれば詳細リストを返す。空リストなら OK。

    チェック項目:
    1. ファイルサイズ（15MB以下 ← Instagram制限）
    2. 動画長（3〜90秒）
    3. 解像度（縦型: 1080×1920 / 正方形: 1080×1080）
    4. フレームレート（24fps以上）
    """
    issues = []

    try:
        from moviepy import VideoFileClip
        clip = VideoFileClip(str(path))

        # 1. ファイルサイズ
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > 15:
            issues.append(f"ファイルサイズ超過: {size_mb:.1f}MB（上限15MB）")

        # 2. 動画長
        if clip.duration < 3:
            issues.append(f"動画が短すぎ: {clip.duration:.1f}秒（最低3秒）")
        if clip.duration > 90:
            issues.append(f"動画が長すぎ: {clip.duration:.1f}秒（上限90秒）")

        # 3. 解像度
        w, h = clip.size
        if (w, h) not in [(1080, 1920), (1080, 1080)]:
            issues.append(f"解像度不正: {w}×{h}（1080×1920 または 1080×1080 が必要）")

        # 4. フレームレート
        if clip.fps < 24:
            issues.append(f"フレームレート不足: {clip.fps}fps（24fps以上必要）")

        clip.close()
    except Exception as e:
        issues.append(f"バリデーション実行エラー: {e}")

    return issues


def generate_video(mode: str = "tiktok", with_voiceover: bool = False):
    """動画を生成して保存する"""
    if mode == "tiktok":
        canvas_w, canvas_h = 1080, 1920
        out_path = VIDEO_DIR / "calopet_tiktok.mp4"
        label    = "縦型 (TikTok / Reels)"
    else:
        canvas_w, canvas_h = 1080, 1080
        out_path = VIDEO_DIR / "calopet_square.mp4"
        label    = "正方形 (Instagram Feed)"

    print(f"\n▶ {label} を生成中...")
    print(f"  解像度: {canvas_w}×{canvas_h}")

    clips = [build_clip(s, canvas_w, canvas_h) for s in SLIDES]
    video = concatenate_videoclips(clips, method="compose")

    # ---- BGM 埋め込み（優先順位: BGM > ボイスオーバー > サイレント）----
    audio_clip = None

    if BGM_PATH and BGM_PATH.exists():
        print(f"  ♪ BGM を埋め込み: {BGM_PATH.name}")
        bgm = AudioFileClip(str(BGM_PATH))
        # 動画より短い場合はループ（最大3回）
        if bgm.duration < video.duration:
            repeats = int(video.duration / bgm.duration) + 1
            from moviepy import concatenate_audioclips
            bgm = concatenate_audioclips([bgm] * min(repeats, 3))
        bgm = bgm.subclipped(0, video.duration)
        # ボリューム調整（声がある場合は下げる、BGMのみなら少し大きめ）
        bgm = bgm.with_effects([])  # moviepy2ではvolumexを直接掛ける
        audio_clip = bgm
    elif with_voiceover:
        vo_path = VIDEO_DIR / "voiceover.mp3"
        if generate_voiceover(VOICEOVER_SCRIPT, vo_path):
            audio_clip = AudioFileClip(str(vo_path))
            if audio_clip.duration > video.duration:
                audio_clip = audio_clip.subclipped(0, video.duration)
    else:
        print("  ⚠️ BGM未配置: SNS/audio/bgm_main.m4a を配置するとBGM付き動画になります")

    if audio_clip:
        video = video.with_audio(audio_clip)

    video.write_videofile(
        str(out_path),
        fps=30,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        logger=None,
    )
    print(f"  ✅ 保存完了: {out_path}")

    # ---- 投稿前バリデーション ----
    issues = validate_video(out_path)
    if issues:
        print(f"\n  ⛔ バリデーション失敗 — 以下の問題を修正してください:")
        for issue in issues:
            print(f"     • {issue}")
        raise RuntimeError(f"動画バリデーション失敗: {issues}")
    else:
        print(f"  ✅ バリデーションOK（解像度・サイズ・長さ・フレームレート）")

    # Dropboxにもコピー
    dropbox_dir = Path("/Users/horibetakuya/Library/CloudStorage/Dropbox/カロリっち/SNS動画")
    dropbox_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(out_path, dropbox_dir / out_path.name)
    print(f"  ✅ Dropbox にも保存: {dropbox_dir / out_path.name}")

    return out_path

# ============================================================
# メイン
# ============================================================

if __name__ == "__main__":
    mode_arg = sys.argv[1] if len(sys.argv) > 1 else "both"

    print("=" * 60)
    print("カロリっち 動画自動生成スクリプト（moviepy）")
    print("=" * 60)

    if mode_arg in ("tiktok", "both", "voiceover"):
        generate_video("tiktok", with_voiceover=(mode_arg == "voiceover"))

    if mode_arg in ("square", "both"):
        generate_video("square", with_voiceover=False)

    print("\n" + "=" * 60)
    print(f"完了！動画保存先: {VIDEO_DIR}")
    print(f"      Dropbox:   /Users/horibetakuya/Library/CloudStorage/Dropbox/カロリっち/SNS動画/")
    print("=" * 60)
