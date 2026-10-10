import argparse
import ctypes
import datetime as dt
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import difflib
import hashlib
from pathlib import Path

try:
    from PIL import Image, ImageGrab, ImageOps, ImageFilter, ImageDraw, ImageFont
except Exception as exc:
    print('缺少截图组件 Pillow。')
    print(exc)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT

def enable_dpi_awareness():
    try:
        # Per-monitor DPI awareness: GetWindowRect and ImageGrab then use the same physical pixels.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

enable_dpi_awareness()
WORK = PROJECT / 'work' / 'wechat_chat_exporter'
EXPORTS = PROJECT / '导出结果'
CONFIG_PATH = PROJECT / 'config.json'
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p), ctypes.c_void_p]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.IsWindowVisible.restype = ctypes.c_bool
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsWindow.restype = ctypes.c_bool
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
user32.PrintWindow.restype = ctypes.c_bool
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_ulong]

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

def cursor_pos():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

def args():
    p = argparse.ArgumentParser()
    p.add_argument('--max-pages', type=int, default=22000, help='最多滚动识别页数，默认给 100000 行留足空间。')
    p.add_argument('--max-lines', type=int, default=100000, help='最多导出的消息行数。')
    p.add_argument('--max-empty-pages', type=int, default=6, help='连续多少页没有新增消息后自动停止，避免微信退出后无限运行。')
    p.add_argument('--target-name', default='', help='聊天对象名称，用于 Markdown/JSON 输出。')
    p.add_argument('--self-position', choices=['left', 'right'], default='', help='我在聊天窗口的哪一侧。')
    p.add_argument('--no-config-prompt', action='store_true', help='不询问身份配置，缺失时使用默认配置。')
    p.add_argument('--keep-txt', action='store_true', help='同时输出 TXT。默认只输出 Markdown。')
    p.add_argument('--output-json', action='store_true', help='同时输出 JSON 和统计 JSON。默认不输出。')
    p.add_argument('--split-md', action='store_true', help='同时按月份拆分 Markdown。默认不拆分。')
    p.add_argument('--scroll', type=int, default=0, help='滚轮格数；0 表示按窗口高度自动滚动约半屏。')
    p.add_argument('--delay', type=float, default=0.4)
    p.add_argument('--keep-screenshots', action='store_true')
    p.add_argument('--crop', default='')
    p.add_argument('--windows-ocr-lang', default='zh-Hans')
    p.add_argument('--ocr-engine', choices=['auto', 'tesseract', 'windows', 'custom'], default='auto')
    p.add_argument('--tesseract-path', default='')
    p.add_argument('--tesseract-lang', default='chi_sim+eng')
    p.add_argument('--custom-ocr-tsv-command', default='', help='自定义 OCR TSV 命令模板。需向 stdout 输出 Tesseract TSV 格式。可用占位：{image} {imageq} {lang} {psm} {mode}。')
    p.add_argument('--custom-ocr-text-command', default='', help='自定义 OCR 纯文本命令模板。用于短文本二次识别，可选。可用占位同 --custom-ocr-tsv-command。')
    p.add_argument('--window-title', default='')
    p.add_argument('--region', default='', help='固定截图区域：left,top,right,bottom。留空则用鼠标框选。')
    p.add_argument('--calibrate', action='store_true', help='只截一张全屏校准图，不做 OCR。')
    p.add_argument('--content-inset', default='0,140,0,260', help='识别区域内再裁剪 left,top,right,bottom，默认避开微信标题和输入框。')
    p.add_argument('--no-auto-chat-panel', action='store_true', help='关闭自动裁剪微信左侧会话列表。')
    p.add_argument('--avatar-strip', type=int, default=125, help='左右两侧头像/边缘屏蔽宽度，减少头像乱码。')
    p.add_argument('--min-conf', type=float, default=18, help='Tesseract 最低置信度。')
    p.add_argument('--dedupe-threshold', type=float, default=0.80, help='跨页去重相似度。')
    p.add_argument('--keep-system', action='store_true', help='保留时间、系统提示等居中文本。默认过滤。')
    p.add_argument('--debug-retain-pages', type=int, default=300, help='结束后保留前 N 张原始页面截图用于排查，默认 300。')
    p.add_argument('--scroll-x-ratio', type=float, default=0.5, help='滚动点击点在区域内的横向比例。')
    p.add_argument('--scroll-y-ratio', type=float, default=0.45, help='滚动点击点在区域内的纵向比例，避开输入框。')
    p.add_argument('--scroll-ratio', type=float, default=0.55, help='自动滚动时每次滚动的窗口高度比例，默认约半屏。')
    p.add_argument('--wheel-pixels-per-notch', type=float, default=24.0, help='估算每格滚轮对应的像素，用于自动滚动。')
    p.add_argument('--scroll-pulse', type=int, default=-5, help='单次滚轮脉冲大小；自动滚动会连续发多个脉冲。')
    p.add_argument('--scroll-pulse-delay', type=float, default=0.04, help='滚轮脉冲之间的间隔秒数。')
    p.add_argument('--edge-margin', type=int, default=110, help='忽略截图内容上下边缘的文字，减少半截气泡。')
    p.add_argument('--ocr-scale', type=float, default=1.8, help='OCR 图片放大倍数，过大容易把 UI 噪声也放大。')
    p.add_argument('--text-threshold', type=int, default=150, help='深色文字提取阈值，越小越严格。')
    p.add_argument('--ocr-thresholds', default='135,150,165,180', help='多阈值 OCR，逗号分隔，用于兼顾绿色气泡、灰字和普通黑字。')
    p.add_argument('--print-window', action='store_true', help='使用 PrintWindow 抓窗口内容；默认使用屏幕截图。')
    return p.parse_args()

def window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value

def desktop_size():
    try:
        img = ImageGrab.grab()
        return img.size
    except Exception:
        return (0, 0)

def window_rect(hwnd):
    r = RECT()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        raise RuntimeError('无法读取窗口位置')
    return (r.left, r.top, r.right, r.bottom)

def is_bad_window_title(title):
    bad = [
        'cmd.exe', 'cmd.', 'powershell', 'terminal', 'system32',
        'wechat_chat_exporter', 'chat_exports', '文件资源管理器',
        'diagnose_ocr', 'run_select_window', 'run_with_fixed_region'
    ]
    low = title.lower()
    return any(x.lower() in low for x in bad)

def list_windows():
    windows = []
    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            title = window_title(hwnd).strip()
            if title and not is_bad_window_title(title):
                try:
                    box = window_rect(hwnd)
                    w = box[2] - box[0]
                    h = box[3] - box[1]
                    if w >= 250 and h >= 200:
                        windows.append((hwnd, title, box))
                except Exception:
                    pass
        return True
    user32.EnumWindows(CALLBACK(enum_proc), 0)
    return windows

def choose_window(title_hint=''):
    windows = list_windows()
    if title_hint:
        matches = [w for w in windows if title_hint.lower() in w[1].lower()]
        if matches:
            if is_bad_window_title(matches[0][1]):
                raise RuntimeError('匹配到的是工具/命令窗口，不是微信窗口：' + matches[0][1])
            return matches[0]
        raise RuntimeError('没有找到标题包含 ' + title_hint + ' 的窗口')

    print('桌面截图尺寸：', desktop_size())
    print('请选择要导出的微信聊天窗口：')
    for i, (hwnd, title, box) in enumerate(windows, 1):
        mark = '  <-- 可能是微信' if ('微信' in title or 'WeChat' in title) else ''
        print(f'{i:2d}. {title}  {box}{mark}')
    choice = input('输入窗口编号，然后按回车：').strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(windows)):
        raise RuntimeError('窗口编号无效')
    hwnd, title, box = windows[int(choice) - 1]
    if is_bad_window_title(title):
        raise RuntimeError('不能选择工具/命令/资源管理器窗口：' + title)
    user32.ShowWindow(hwnd, 5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    return hwnd, title, window_rect(hwnd)

def capture_window_image(hwnd, box, force_screen=False):
    if force_screen or not hwnd:
        return ImageGrab.grab(bbox=box)
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        return ImageGrab.grab(bbox=box)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        ok = user32.PrintWindow(hwnd, mem_dc, 2) or user32.PrintWindow(hwnd, mem_dc, 0)
        if not ok:
            return ImageGrab.grab(bbox=box)
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32), ('biHeight', ctypes.c_int32),
                ('biPlanes', ctypes.c_uint16), ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32), ('biYPelsPerMeter', ctypes.c_int32),
                ('biClrUsed', ctypes.c_uint32), ('biClrImportant', ctypes.c_uint32)
            ]
        class BITMAPINFO(ctypes.Structure):
            _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', ctypes.c_uint32 * 3)]
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(width * height * 4)
        got = gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bmi), 0)
        if not got:
            return ImageGrab.grab(bbox=box)
        img = Image.frombuffer('RGBA', (width, height), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
        return img
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)

def crop_bbox(b, c):
    if not c:
        return b
    nums = [int(x.strip()) for x in c.split(',')]
    if len(nums) != 4:
        raise RuntimeError('--crop 格式应为 left,top,right,bottom')
    return (b[0]+nums[0], b[1]+nums[1], b[0]+nums[2], b[1]+nums[3])


def find_tesseract(custom_path=''):
    if custom_path:
        p = Path(custom_path)
        if p.exists():
            return str(p)
    exe = shutil.which('tesseract')
    if exe:
        return exe
    candidates = [
        Path(os.environ.get('ProgramFiles', '')) / 'Tesseract-OCR' / 'tesseract.exe',
        Path(os.environ.get('ProgramFiles(x86)', '')) / 'Tesseract-OCR' / 'tesseract.exe',
        Path.home() / 'AppData' / 'Local' / 'Programs' / 'Tesseract-OCR' / 'tesseract.exe',
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None

def ocr_tesseract(image_path, exe, lang):
    cmd = [exe, str(image_path), 'stdout', '-l', lang, '--psm', '6', '--oem', '1']
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError('Tesseract OCR failed. ' + (r.stderr or r.stdout or '').strip())
    return r.stdout

def ocr_tesseract_tsv(image_path, exe, lang):
    cmd = [exe, str(image_path), 'stdout', '-l', lang, '--psm', '6', '--oem', '1', 'tsv']
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError('Tesseract TSV failed. ' + (r.stderr or r.stdout or '').strip())
    return r.stdout

def ocr_tesseract_text_psm(image_path, exe, lang, psm='7'):
    cmd = [exe, str(image_path), 'stdout', '-l', lang, '--psm', str(psm), '--oem', '1']
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        return ''
    return r.stdout

def quoted_command_arg(value):
    return subprocess.list2cmdline([str(value)])

def format_custom_ocr_command(template, image_path, lang, psm='6', mode='tsv'):
    if not template:
        raise RuntimeError('未配置自定义 OCR 命令。请设置 --custom-ocr-tsv-command。')
    values = {
        'image': str(image_path),
        'imageq': quoted_command_arg(image_path),
        'lang': lang,
        'psm': str(psm),
        'mode': mode,
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise RuntimeError(f'自定义 OCR 命令包含未知占位：{exc}') from exc

def run_custom_ocr_command(template, image_path, lang, psm='6', mode='tsv'):
    cmd = format_custom_ocr_command(template, image_path, lang, psm, mode)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', shell=True)
    if r.returncode != 0:
        raise RuntimeError('Custom OCR failed. ' + (r.stderr or r.stdout or '').strip())
    return r.stdout

def run_ocr_tsv(image_path, a, tesseract_exe):
    if a.ocr_engine == 'custom':
        return run_custom_ocr_command(a.custom_ocr_tsv_command, image_path, a.tesseract_lang, '6', 'tsv')
    return ocr_tesseract_tsv(image_path, tesseract_exe, a.tesseract_lang)

def run_ocr_text_psm(image_path, a, tesseract_exe, psm='7'):
    if a.ocr_engine == 'custom':
        if not a.custom_ocr_text_command:
            return ''
        try:
            return run_custom_ocr_command(a.custom_ocr_text_command, image_path, a.tesseract_lang, psm, 'text')
        except RuntimeError:
            return ''
    return ocr_tesseract_text_psm(image_path, tesseract_exe, a.tesseract_lang, psm)

def parse_inset(text):
    nums = [int(x.strip()) for x in text.split(',')]
    if len(nums) != 4:
        raise RuntimeError('--content-inset 格式应为 left,top,right,bottom')
    return nums

def image_pixels(image):
    getter = getattr(image, 'get_flattened_data', None)
    if getter:
        return list(getter())
    return list(image.getdata())

def detect_wechat_chat_panel_left(img, a):
    if getattr(a, 'no_auto_chat_panel', False):
        return 0
    w, h = img.size
    if w < 1500:
        return 0
    sample = img.crop((0, 0, int(w * 0.45), h)).resize((120, 120)).convert('RGB')
    pixels = image_pixels(sample)
    if not pixels:
        return 0
    green_ratio = sum(
        1
        for r, g, b in pixels
        if g > 130 and b > 55 and r < 90 and g - r > 60 and g - b > 20
    ) / len(pixels)
    if green_ratio < 0.002:
        return 0
    return int(w * 0.34)

def content_crop_box(img, a):
    w, h = img.size
    inset_left, inset_top, inset_right, inset_bottom = parse_inset(a.content_inset)
    auto_left = detect_wechat_chat_panel_left(img, a)
    left = max(0, inset_left, auto_left)
    top = max(0, inset_top)
    right = max(left + 80, w - max(0, inset_right))
    bottom = max(top + 80, h - max(0, inset_bottom))
    return left, top, right, bottom

def screenshot_difference_score(a_img, b_img):
    a_small = ImageOps.grayscale(a_img.resize((64, 64)))
    b_small = ImageOps.grayscale(b_img.resize((64, 64)))
    a_pixels = image_pixels(a_small)
    b_pixels = image_pixels(b_small)
    if not a_pixels or len(a_pixels) != len(b_pixels):
        return 255.0
    return sum(abs(a - b) for a, b in zip(a_pixels, b_pixels)) / len(a_pixels)

def looks_like_tsv_dump(text):
    compact = re.sub(r'\s+', ' ', text or '').strip()
    if not compact:
        return False
    row_hits = len(re.findall(r'(?:^|\s)[45]\s+1\s+1\s+1\s+\d+\s+\d+', compact))
    numeric_hits = len(re.findall(r'\b\d+(?:\.\d+)?\b', compact))
    token_count = len(compact.split())
    if row_hits >= 2:
        return True
    if numeric_hits >= 35 and token_count >= 60:
        return True
    if ' 5 1 1 1 ' in compact and numeric_hits >= 18:
        return True
    return False

def text_has_signal(text):
    if not text:
        return False
    if looks_like_tsv_dump(text):
        return False
    zh = re.findall(r'[\u4e00-\u9fff]', text)
    alnum = re.findall(r'[A-Za-z0-9]', text)
    if not zh and not alnum:
        return False
    if len(zh) == 0 and re.fullmatch(r'[0-9０-９]{1,3}', text.strip()):
        return True
    if len(zh) == 0 and len(text.strip()) <= 10:
        return False
    if len(zh) == 0 and re.fullmatch(r'[A-Za-z0-9\s,.;:|\\/_\-—=]+', text.strip()):
        return False
    if len(zh) == 0 and len(alnum) <= 3:
        return False
    noisy = re.sub(r'[\u4e00-\u9fffA-Za-z0-9]', '', text)
    return len(noisy) <= max(8, len(text) * 1.2)

def join_words(words):
    text = ' '.join(words)
    text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def is_system_text(text):
    compact = re.sub(r'\s+', '', text)
    compact = compact.strip('-—_.，,。:：|丨')
    compact_plain = re.sub(r'[，,。:：；;！？?!、\s]+', '', compact)
    compact = re.sub(r'(?<=\d)[.·](?=\d{2}$)', ':', compact)
    if re.fullmatch(r'\d{1,2}月\d{1,2}日(\d{1,2}:\d{2})?', compact):
        return True
    if re.fullmatch(r'\d{4}年\d{1,2}月\d{1,2}日(\d{1,2}:\d{2})?', compact):
        return True
    if re.fullmatch(r'年\d{1,2}月\d{1,2}日(\d{1,2}:\d{2})?', compact):
        return True
    if re.fullmatch(r'(昨天|今天|前天)?\d{1,2}:\d{2}', compact):
        return True
    if re.fullmatch(r'星期[一二三四五六日天]\d{1,2}:\d{2}', compact):
        return True
    if re.fullmatch(r'\d{0,2}月\d{1,2}[日号衣]?\d{1,2}:\d{2}', compact):
        return True
    if re.fullmatch(r'\d{0,2}月\d{1,2}[日号衣]?\d{2,3}:\d{2}', compact):
        return True
    if re.fullmatch(r'[3S5]?\d{2,5}\d{1,2}:\d{2}', compact):
        return True
    if re.fullmatch(r'[3S5]月?\d{1,2}[日号]?\d{1,2}:\d{2}', compact):
        return True
    system_phrases = ['以上是打招呼的消息', '你已添加了现在可以开始聊天了']
    if any(p in compact_plain for p in system_phrases):
        return True
    if '你已添加了' in compact_plain and '开始' in compact_plain:
        return True
    return False

def tsv_lines(tsv_text, scale, offset, img_width, img_height, a):
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter='\t')
    grouped = {}
    for row in reader:
        if row.get('level') != '5':
            continue
        word = (row.get('text') or '').strip()
        if not word:
            continue
        try:
            conf = float(row.get('conf') or -1)
        except ValueError:
            conf = -1
        if conf >= 0 and conf < a.min_conf:
            continue
        key_id = (row.get('block_num'), row.get('par_num'), row.get('line_num'))
        item = grouped.setdefault(key_id, {'words': [], 'left': 10**9, 'top': 10**9, 'right': 0, 'bottom': 0})
        x = int(float(row.get('left') or 0)) / scale + offset[0]
        y = int(float(row.get('top') or 0)) / scale + offset[1]
        ww = int(float(row.get('width') or 0)) / scale
        hh = int(float(row.get('height') or 0)) / scale
        item['words'].append(word)
        item['left'] = min(item['left'], x)
        item['top'] = min(item['top'], y)
        item['right'] = max(item['right'], x + ww)
        item['bottom'] = max(item['bottom'], y + hh)

    lines = []
    for item in grouped.values():
        top_margin = max(25, min(a.edge_margin, 55))
        bottom_margin = max(25, min(a.edge_margin, 35))
        if item['top'] < offset[1] + top_margin or item['bottom'] > img_height - bottom_margin:
            continue
        text = join_words(item['words'])
        if not text_has_signal(text):
            continue
        center_x = (item['left'] + item['right']) / 2
        panel_mid = (offset[0] + img_width) / 2
        if is_system_text(text):
            speaker = '系统'
        elif center_x < panel_mid:
            speaker = '头像1'
        elif center_x > panel_mid:
            speaker = '头像2'
        else:
            speaker = '系统'
        lines.append({'speaker': speaker, 'text': text, 'top': item['top'], 'bottom': item['bottom'], 'left': item['left'], 'right': item['right']})
    lines.sort(key=lambda x: (x['top'], x['left']))
    return lines

def apply_common_ocr_fixes(text):
    # Keep this generic. Domain-specific typo fixes belong outside the core OCR
    # pipeline; this function should only remove common OCR residue.
    text = re.sub(r'([一-龥])\s+7\s*吗$', r'\1了吗', text)
    text = re.sub(r'很\s*[Xx]\s*了\s*7$', '很久了', text)
    text = re.sub(r'\s+[7Xx]$', '', text).strip()
    text = re.sub(r'(.+)了了吗$', r'\1了吗', text)
    return text

def clean_message_text(text):
    text = re.sub(r'\s+', ' ', text).strip()
    if looks_like_tsv_dump(text):
        return ''
    compact = re.sub(r'\s+', '', text)
    if is_system_text(compact):
        return ''
    if re.fullmatch(r'[\u4e00-\u9fff]{1,2}\s+[A-Za-z0-9]{2,}\s+\d{1,2}[-:]\d{2}', text):
        return ''
    # Remove bubble-tail/avatar-edge artifacts at the end.
    text = re.sub(r'[\s\|丨\\/\]\[{}<>_=~`^"“”‘’.,，。;；:：-]+$', '', text).strip()
    # Remove a short Latin/symbol fragment before the first Chinese character.
    text = re.sub(r'^[A-Za-z0-9&@#*+\-_=~`^\|丨\\/.,，。;；:：\s]{1,6}(?=[一-龥])', '', text).strip()
    # Remove isolated leading punctuation that often comes from emoji/avatar boundaries.
    text = re.sub(r'^[^一-龥A-Za-z0-9]{1,4}(?=[一-龥])', '', text).strip()
    # Normalize common OCR punctuation spacing.
    text = text.replace(' ,', '，').replace(',', '，')
    text = re.sub(r'\s+([？?!！。，])', r'\1', text)
    text = re.sub(r'([一-龥])\s+([一-龥])', r'\1\2', text)
    text = apply_common_ocr_fixes(text)
    return text

def clean_message_line(line):
    if '：' not in line:
        return clean_message_text(line)
    speaker, text = line.split('：', 1)
    cleaned = clean_message_text(text)
    return f'{speaker}：{cleaned}' if cleaned else ''

def detect_text_bubbles(img, a):
    left, top, right, bottom = content_crop_box(img, a)
    step = 4
    small = img.crop((left, top, right, bottom)).convert('RGB')
    small = small.resize((max(1, small.width // step), max(1, small.height // step)), Image.Resampling.NEAREST)
    w, h = small.size
    mask = bytearray(w * h)
    for index, (r, g, b) in enumerate(image_pixels(small)):
        if g > 185 and 115 < r < 215 and 115 < b < 215 and g - max(r, b) > 25:
            mask[index] = 1
        elif 225 <= min(r, g, b) and max(r, g, b) <= 244 and max(r, g, b) - min(r, g, b) <= 6:
            mask[index] = 2
    bubbles = []
    for seed in range(len(mask)):
        kind = mask[seed]
        if not kind:
            continue
        mask[seed] = 0
        stack = [seed]
        x1 = x2 = seed % w
        y1 = y2 = seed // w
        count = 0
        while stack:
            index = stack.pop()
            x, y = index % w, index // w
            x1, x2 = min(x1, x), max(x2, x)
            y1, y2 = min(y1, y), max(y2, y)
            count += 1
            neighbors = []
            if x: neighbors.append(index - 1)
            if x + 1 < w: neighbors.append(index + 1)
            if y: neighbors.append(index - w)
            if y + 1 < h: neighbors.append(index + w)
            for neighbor in neighbors:
                if mask[neighbor] == kind:
                    mask[neighbor] = 0
                    stack.append(neighbor)
        bw, bh = (x2 - x1 + 1) * step, (y2 - y1 + 1) * step
        if bw < 45 or bh < 28 or count / ((x2 - x1 + 1) * (y2 - y1 + 1)) < 0.45:
            continue
        box = [left + x1 * step, top + y1 * step, left + (x2 + 1) * step, top + (y2 + 1) * step]
        if kind == 1 and box[2] < right - (right - left) * 0.23:
            continue
        if kind == 2 and box[0] > left + (right - left) * 0.23:
            continue
        bubbles.append({'bbox': box, 'speaker': '头像2' if kind == 1 else '头像1',
                        'complete': box[1] > top + step and box[3] < bottom - step})
    return sorted(bubbles, key=lambda item: (item['bbox'][1], item['bbox'][0]))


def assign_lines_to_bubbles(lines, bubbles, page):
    for line in lines:
        if line.get('kind') == 'time' or line.get('speaker') == '系统':
            continue
        x1, y1, x2, y2 = (line.get(key, 0) for key in ('left', 'top', 'right', 'bottom'))
        area = max(1, (x2 - x1) * (y2 - y1))
        matches = []
        for index, bubble in enumerate(bubbles):
            bx1, by1, bx2, by2 = bubble['bbox']
            overlap = max(0, min(x2, bx2) - max(x1, bx1)) * max(0, min(y2, by2) - max(y1, by1))
            if overlap / area >= 0.85:
                matches.append((index, bubble))
        if len(matches) == 1:
            index, bubble = matches[0]
            line['speaker_original'] = line['speaker']
            line['speaker'] = bubble['speaker']
            line['bubble_id'] = f'{page}:{index}'
            line['bubble_bbox'] = list(bubble['bbox'])
            line['bubble_complete'] = bubble['complete']
            line['sender_evidence'] = 'bubble_geometry'
    return lines


def classify_auxiliary_lines(img, lines, bubbles):
    pixels = img.convert('RGB')
    for line in lines:
        if line.get('bubble_id') or line.get('kind') == 'time':
            continue
        compact = re.sub(r'\s+', '', line.get('text', ''))
        if '撤回了一条消息' in compact or '撤回消息' in compact:
            line.update(speaker='系统', kind='recall', sender_evidence='system_notice')
            continue
        x1, y1, x2, y2 = (int(line.get(key, 0)) for key in ('left', 'top', 'right', 'bottom'))
        crop = pixels.crop((max(0, x1), max(0, y1), min(img.width, x2), min(img.height, y2)))
        ink = [rgb for rgb in image_pixels(crop) if max(rgb) <= 205]
        if len(ink) < 15 or sum(min(rgb) >= 125 for rgb in ink) / len(ink) < 0.70:
            continue
        line['structure_warning'] = '浅灰文字不属于已确认气泡，可能是引用或系统提示；不能仅按文字中心确认发送者'
        line['structure_category'] = '疑似发送者归属异常'
        for bubble in bubbles:
            bx1, by1, bx2, by2 = bubble['bbox']
            if not (0 <= y1 - by2 <= 125 and x1 >= bx1 - 12 and x2 <= bx2 + 12):
                continue
            edge = bx2 if bubble['speaker'] == '头像2' else bx1
            # A pale vertical reply bar plus light text outside the parent bubble.
            bar = False
            for x in range(max(0, edge - 12), min(img.width, edge + 13)):
                count = sum(200 <= min(pixels.getpixel((x, y))) <= 235
                            and max(pixels.getpixel((x, y))) - min(pixels.getpixel((x, y))) <= 6
                            for y in range(max(0, y1), min(img.height, y2)))
                if count >= max(15, (y2 - y1) * 0.65):
                    bar = True
                    break
            if bar:
                line.update(speaker='系统', kind='quoted_context', sender_evidence='reply_bar_geometry')
                break
    return lines


def recover_unread_bubbles(img, lines, bubbles, a, tesseract_exe, page, shots):
    for index, bubble in enumerate(bubbles):
        bubble_id = f'{page}:{index}'
        if any(line.get('bubble_id') == bubble_id for line in lines):
            continue
        x1, y1, x2, y2 = bubble['bbox']
        covered = False
        for line in lines:
            lx1, ly1, lx2, ly2 = (line.get(key, 0) for key in ('left', 'top', 'right', 'bottom'))
            overlap = max(0, min(x2, lx2) - max(x1, lx1)) * max(0, min(y2, ly2) - max(y1, ly1))
            if overlap / max(1, (lx2 - lx1) * (ly2 - ly1)) >= 0.50:
                covered = True
                break
        if covered:
            continue
        if not bubble['complete'] or y2 - y1 > 260:
            continue
        crop = img.crop((x1 + 12, y1 + 8, x2 - 12, y2 - 8)).convert('RGB')
        if crop.width < 10 or crop.height < 10:
            continue
        if sum(max(rgb) < 100 for rgb in image_pixels(crop)) < 20:
            continue
        text_image = ImageOps.invert(extract_dark_text_image(crop, 150))
        text_image = ImageOps.expand(text_image, border=10, fill=255)
        text_image = text_image.resize((text_image.width * 2, text_image.height * 2))
        path = shots / f'page_{page:03d}_unread_bubble_{index}.png'
        text_image.save(path)
        text = clean_message_text(run_ocr_text_psm(path, a, tesseract_exe, '6'))
        if not text_has_signal(text) or refine_candidate_score(text) < 0:
            text = '[文字未识别]'
        lines.append({'speaker': bubble['speaker'], 'text': text,
                      'left': x1, 'top': y1, 'right': x2, 'bottom': y2,
                      'bubble_id': bubble_id, 'bubble_bbox': list(bubble['bbox']),
                      'bubble_complete': bubble['complete'], 'sender_evidence': 'bubble_geometry',
                      'structure_warning': '整页 OCR 漏掉可见文字气泡；已局部重识别，请核对原图',
                      **({'kind': 'unread_text'} if text == '[文字未识别]' else {})})
    return sorted(lines, key=lambda line: (line.get('top', 0), line.get('left', 0)))


def merge_message_lines(lines, a, page=0):
    messages = []
    for line in lines:
        if line.get('kind') in ('time', 'recall', 'quoted_context', 'unread_text'):
            messages.append(dict(line))
            continue
        same_bubble = bool(messages and line.get('bubble_id') and messages[-1].get('bubble_id') == line['bubble_id'])
        different_bubbles = bool(messages and line.get('bubble_id') and messages[-1].get('bubble_id')
                                 and not same_bubble)
        if messages and messages[-1]['speaker'] == line['speaker'] and not different_bubbles and (same_bubble or line['top'] - messages[-1]['bottom'] < 28):
            messages[-1]['text'] += ' ' + clean_message_text(line['text'])
            messages[-1]['bottom'] = max(messages[-1]['bottom'], line['bottom'])
            messages[-1]['left'] = min(messages[-1].get('left', line.get('left', 0)), line.get('left', 0))
            messages[-1]['right'] = max(messages[-1].get('right', line.get('right', 0)), line.get('right', 0))
        else:
            new_line = dict(line)
            new_line['text'] = clean_message_text(new_line['text'])
            if not new_line['text']:
                continue
            messages.append(new_line)
    output = []
    for m in messages:
        if not m['text']:
            continue
        if m.get('kind') in ('recall', 'quoted_context', 'unread_text'):
            item = dict(m)
            item.update(line=f"{m['speaker']}：{m['text']}", page=page)
            output.append(item)
            continue
        if m['speaker'] == '系统':
            output.append({
                'line': f"系统：{m['text']}",
                'speaker': '系统',
                'text': m['text'],
                'top': m.get('top', 0),
                'bottom': m.get('bottom', 0),
                'left': m.get('left', 0),
                'right': m.get('right', 0),
                'page': page,
                'kind': 'time',
                'time_raw': m.get('time_raw', m['text']),
                'time_rejected': m.get('time_rejected', ''),
            })
            continue
        line_text = f"{m['speaker']}：{m['text']}"
        output.append({
            'line': line_text,
            'speaker': m['speaker'],
            'text': m['text'],
            'top': m.get('top', 0),
            'bottom': m.get('bottom', 0),
            'left': m.get('left', 0),
            'right': m.get('right', 0),
            'page': page,
            'bubble_id': m.get('bubble_id'),
            'bubble_bbox': m.get('bubble_bbox'),
            'bubble_complete': m.get('bubble_complete'),
            'sender_evidence': m.get('sender_evidence', 'text_center'),
            'structure_warning': m.get('structure_warning', ''),
            'structure_category': m.get('structure_category', ''),
        })
    return output

def extract_dark_text_image(content, threshold):
    # Convert WeChat bubbles to black text on white background.
    # This removes green/gray bubble color while preserving dark Chinese glyphs.
    rgb = content.convert('RGB')
    gray = ImageOps.grayscale(rgb)
    mask = gray.point(lambda p: 0 if p < threshold else 255, mode='1')
    out = Image.new('L', rgb.size, 255)
    out.paste(0, mask=mask)
    return out

def prepare_text_image(img, page, shots, a, threshold=None, suffix='text'):
    w, h = img.size
    left, top, right, bottom = content_crop_box(img, a)
    content = img.crop((left, top, right, bottom)).convert('RGB')

    draw = ImageDraw.Draw(content)
    strip = max(0, min(a.avatar_strip, content.size[0] // 4))
    if strip:
        # Use page background color, not pure white, before thresholding.
        draw.rectangle((0, 0, strip, content.size[1]), fill=(245, 245, 245))
        draw.rectangle((content.size[0] - strip, 0, content.size[0], content.size[1]), fill=(245, 245, 245))

    text_img = extract_dark_text_image(content, int(threshold if threshold is not None else a.text_threshold))
    text_img = text_img.filter(ImageFilter.SHARPEN)
    scale = max(1.0, min(2.2, float(a.ocr_scale)))
    scaled = text_img.resize((int(text_img.size[0] * scale), int(text_img.size[1] * scale)))
    out = shots / f'page_{page:03d}_{suffix}_ocr.png'
    scaled.save(out)
    return out, scale, (left, top)

def parse_ocr_thresholds(a):
    values = []
    for part in str(getattr(a, 'ocr_thresholds', '') or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(max(80, min(230, int(part))))
        except ValueError:
            pass
    if not values:
        values = [int(getattr(a, 'text_threshold', 150))]
    if int(getattr(a, 'text_threshold', 150)) not in values:
        values.insert(0, int(getattr(a, 'text_threshold', 150)))
    return list(dict.fromkeys(values))

def line_candidate_score(line):
    text = clean_message_text(line.get('text', ''))
    if not text:
        return -1000
    zh = len(re.findall(r'[\u4e00-\u9fff]', text))
    alnum = len(re.findall(r'[A-Za-z0-9]', text))
    noise = len(re.findall(r'[|丨\\\\/_=~`^{}\\[\\]<>]', text))
    score = zh * 5 + alnum - noise * 5
    if line.get('speaker') == '系统':
        score -= 2
    return score

def line_overlap_ratio(a_line, b_line):
    ax1, ay1, ax2, ay2 = a_line.get('left', 0), a_line.get('top', 0), a_line.get('right', 0), a_line.get('bottom', 0)
    bx1, by1, bx2, by2 = b_line.get('left', 0), b_line.get('top', 0), b_line.get('right', 0), b_line.get('bottom', 0)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    return inter / min(area_a, area_b)

def merge_ocr_line_candidates(lines):
    kept = []
    for line in sorted(lines, key=lambda item: line_candidate_score(item), reverse=True):
        if line_candidate_score(line) < 0:
            continue
        duplicate_index = None
        for idx, old in enumerate(kept):
            if line.get('bubble_id') and old.get('bubble_id') and line['bubble_id'] != old['bubble_id']:
                continue
            same_speaker_line = line.get('speaker') == old.get('speaker')
            close_y = abs(line.get('top', 0) - old.get('top', 0)) <= 28
            overlap = line_overlap_ratio(line, old)
            sim = similarity(
                re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', clean_message_text(line.get('text', '')).lower()),
                re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', clean_message_text(old.get('text', '')).lower()),
            )
            if same_speaker_line and (overlap >= 0.45 or (close_y and sim >= 0.45)):
                duplicate_index = idx
                break
        if duplicate_index is None:
            kept.append(line)
    return sorted(kept, key=lambda item: (item.get('top', 0), item.get('left', 0)))

def compact_text_value(text):
    return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', (text or '').lower())

def refine_candidate_score(text):
    cleaned = clean_message_text(text)
    if not cleaned or is_system_text(cleaned):
        return -1000
    zh = len(re.findall(r'[\u4e00-\u9fff]', cleaned))
    latin = len(re.findall(r'[A-Za-z]', cleaned))
    digit = len(re.findall(r'\d', cleaned))
    noise = len(re.findall(r'[|丨\\\\/_=~`^{}\\[\\]<>]', cleaned))
    return zh * 6 - latin * 8 - noise * 10 - digit

def reocr_bubble_text(img, message, a, tesseract_exe, page, index):
    if not tesseract_exe or message.get('kind') or message.get('speaker') != '头像2':
        return ''
    text = message.get('text', '')
    compact = compact_text_value(text)
    if len(compact) < 2 or len(compact) > 5:
        return ''
    w, h = img.size
    x1 = max(0, int(message.get('left', 0)) - 75)
    y1 = max(0, int(message.get('top', 0)) - 45)
    x2 = min(w, int(message.get('right', 0)) - 6)
    y2 = min(h, int(message.get('bottom', 0)) + 32)
    if x2 <= x1 + 40 or y2 <= y1 + 25:
        return ''
    crop = img.crop((x1, y1, x2, y2)).convert('RGB')
    temp_dir = Path(tempfile.gettempdir())
    best = ''
    best_score = -1000
    for threshold in (120, 135, 150, 165, 180):
        gray = ImageOps.grayscale(crop)
        bw = Image.new('L', gray.size, 255)
        mask = gray.point(lambda p, t=threshold: 0 if p < t else 255, mode='1')
        bw.paste(0, mask=mask)
        bw = bw.filter(ImageFilter.SHARPEN)
        scaled = bw.resize((int(bw.size[0] * 3), int(bw.size[1] * 3)))
        path = temp_dir / f'wechat_bubble_reocr_{os.getpid()}_{page}_{index}_{threshold}.png'
        try:
            scaled.save(path)
            candidate = clean_message_text(run_ocr_text_psm(path, a, tesseract_exe, '7'))
        finally:
            path.unlink(missing_ok=True)
        candidate_compact = compact_text_value(candidate)
        if not candidate_compact:
            continue
        sim = similarity(compact, candidate_compact)
        if sim < 0.45:
            continue
        score = refine_candidate_score(candidate)
        if len(candidate_compact) >= len(compact):
            score += 2
        if score > best_score:
            best = candidate
            best_score = score
    if best and best != text and best_score >= refine_candidate_score(text) - 2:
        return best
    return ''

def refine_short_text_messages(img, messages, a, tesseract_exe, page):
    refined = []
    for index, message in enumerate(messages):
        item = dict(message)
        replacement = reocr_bubble_text(img, item, a, tesseract_exe, page, index)
        if replacement:
            item['text'] = replacement
            item['line'] = f"{item.get('speaker', '')}：{replacement}"
        refined.append(item)
    return refined

def prepare_time_image(img, page, shots, a):
    w, h = img.size
    left, top, right, bottom = content_crop_box(img, a)
    content = img.crop((left, top, right, bottom)).convert('RGB')
    gray = ImageOps.grayscale(content)
    out = Image.new('L', gray.size, 255)
    # WeChat time separators are light gray. This threshold keeps them readable
    # without weakening the darker bubble-text OCR path.
    mask = gray.point(lambda p: 0 if p < 175 else 255, mode='1')
    out.paste(0, mask=mask)
    draw = ImageDraw.Draw(out)
    cw, ch = out.size
    draw.rectangle((0, 0, int(cw * 0.25), ch), fill=255)
    draw.rectangle((int(cw * 0.75), 0, cw, ch), fill=255)
    scale = max(1.0, min(2.2, float(a.ocr_scale)))
    scaled = out.resize((int(out.size[0] * scale), int(out.size[1] * scale)))
    path = shots / f'page_{page:03d}_time_ocr.png'
    scaled.save(path)
    return path, scale, (left, top)

def normalize_time_text(text, words=None):
    items = [str(w).strip() for w in (words or []) if str(w).strip()]
    value = re.sub(r'\s+', ' ', text or '').strip()
    compact_value = re.sub(r'\s+', '', value)
    compact_value = re.sub(r'(?<=\d)[.·](?=\d{2}$)', ':', compact_value)
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:(\d{1,2}):(\d{2}))?', compact_value)
    if m:
        if m.group(4):
            return f'{m.group(1)} 年 {m.group(2)} 月 {m.group(3)} 日 {m.group(4)}:{m.group(5)}'
        return f'{m.group(1)} 年 {m.group(2)} 月 {m.group(3)} 日'
    m = re.search(r'年(\d{1,2})月(\d{1,2})日(?:(\d{1,2}):(\d{2}))?', compact_value)
    if m:
        if m.group(3):
            return f'{m.group(1)} 月 {m.group(2)} 日 {m.group(3)}:{m.group(4)}'
        return f'{m.group(1)} 月 {m.group(2)} 日'
    if len(items) >= 3 and re.fullmatch(r'\d{1,2}', items[0]) and re.fullmatch(r'\d{1,2}', items[1]) and re.fullmatch(r'\d{1,2}[:.·]\d{2}', items[2]):
        return f'{items[0]} 月 {items[1]} 日 {items[2].replace(".", ":").replace("·", ":")}'
    if len(items) >= 2 and re.fullmatch(r'\d{1,2}[:.·]\d{2}', items[1]):
        digits = re.sub(r'\D', '', items[0])
        clock = items[1].replace('.', ':').replace('·', ':')
        if len(digits) >= 8 and digits[:2] in ('19', '20'):
            year = int(digits[:4])
            month = int(digits[4:6])
            day = int(digits[6:8])
            if 1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31:
                return f'{year} 年 {month} 月 {day} 日 {clock}'
        for start in range(0, max(0, len(digits) - 2)):
            for day_len in (2, 1):
                month_text = digits[start:start + 1]
                day_text = digits[start + 1:start + 1 + day_len]
                if not month_text or not day_text:
                    continue
                month, day = int(month_text), int(day_text)
                if 1 <= month <= 9 and 1 <= day <= 31:
                    return f'{month} 月 {day} 日 {clock}'
    compact = compact_value
    m = re.fullmatch(r'(\d{1,2})(\d{1,2})(\d{1,2}:\d{2})', compact)
    if m:
        month, day, clock = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f'{month} 月 {day} 日 {clock}'
    return value

def tsv_time_lines(tsv_text, scale, offset, img_height, a, page):
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter='\t')
    word_items = []
    for row in reader:
        if row.get('level') != '5':
            continue
        word = (row.get('text') or '').strip()
        if not word:
            continue
        try:
            conf = float(row.get('conf') or -1)
        except ValueError:
            conf = -1
        if conf >= 0 and conf < 35:
            continue
        x = int(float(row.get('left') or 0)) / scale + offset[0]
        y = int(float(row.get('top') or 0)) / scale + offset[1]
        ww = int(float(row.get('width') or 0)) / scale
        hh = int(float(row.get('height') or 0)) / scale
        word_items.append({'word': word, 'left': x, 'top': y, 'right': x + ww, 'bottom': y + hh, 'center': y + hh / 2})
    clusters = []
    for word in sorted(word_items, key=lambda item: (item['center'], item['left'])):
        if word['top'] < offset[1] + 20 or word['bottom'] > img_height - a.edge_margin:
            continue
        target = None
        for cluster in clusters:
            if abs(word['center'] - cluster['center']) <= 26:
                target = cluster
                break
        if target is None:
            target = {'words': [], 'left': 10**9, 'top': 10**9, 'right': 0, 'bottom': 0, 'center': word['center']}
            clusters.append(target)
        target['words'].append(word)
        target['center'] = (target['center'] * (len(target['words']) - 1) + word['center']) / len(target['words'])
        target['left'] = min(target['left'], word['left'])
        target['top'] = min(target['top'], word['top'])
        target['right'] = max(target['right'], word['right'])
        target['bottom'] = max(target['bottom'], word['bottom'])
    lines = []
    for item in clusters:
        words = [w['word'] for w in sorted(item['words'], key=lambda w: w['left'])]
        raw_text = join_words(words)
        text = normalize_time_text(raw_text, words)
        compact = re.sub(r'\s+', '', text)
        compact = re.sub(r'(?<=\d)[.·](?=\d{2}$)', ':', compact)
        if (
            re.search(r'\d{1,2}月\d{1,2}日(?:\d{1,2}:\d{2})?', compact)
            or re.search(r'\d{4}年\d{1,2}月\d{1,2}日(?:\d{1,2}:\d{2})?', compact)
            or re.search(r'年\d{1,2}月\d{1,2}日(?:\d{1,2}:\d{2})?', compact)
            or re.fullmatch(r'(今天|昨天|前天)?\d{1,2}:\d{2}', compact)
            or re.fullmatch(r'星期[一二三四五六日天]\d{1,2}:\d{2}', compact)
            or re.fullmatch(r'\d{0,2}月\d{1,2}[日号衣]?\d{1,2}:\d{2}', compact)
        ):
            lines.append({
                'line': f'系统：{text}',
                'speaker': '系统',
                'text': text,
                'top': item['top'],
                'bottom': item['bottom'],
                'left': item['left'],
                'right': item['right'],
                'page': page,
                'kind': 'time',
                'time_raw': raw_text,
            })
    lines.sort(key=lambda x: (x['top'], x['left']))
    return lines

def clock_minutes(text):
    compact = re.sub(r'\s+', '', text or '')
    compact = re.sub(r'(?<=\d)[.·](?=\d{2}$)', ':', compact)
    m = re.search(r'(\d{1,2}):(\d{2})$', compact)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour * 60 + minute
    return None

def explicit_month_day_time(text):
    compact = re.sub(r'\s+', '', text or '')
    compact = re.sub(r'(?<=\d)[.·](?=\d{2}$)', ':', compact)
    m = re.fullmatch(r'(?:\d{4}年)?(\d{1,2})月(\d{1,2})日(\d{1,2}):(\d{2})', compact)
    if not m:
        return None
    month, day, hour, minute = map(int, m.groups())
    if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59:
        return month, day, hour * 60 + minute
    return None

def enrich_page_time_lines(time_lines):
    for line in time_lines:
        line.setdefault('time_raw', line.get('text', ''))
    return time_lines

def ocr_windows(image_path, lang):
    ps = ROOT / 'windows_ocr.ps1'
    out_path = image_path.with_suffix('.txt')
    cmd = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps), '-ImagePath', str(image_path), '-Language', lang, '-OutputPath', str(out_path)]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or '').strip()
        raise RuntimeError('Windows OCR failed. ' + detail)
    if out_path.exists():
        return out_path.read_text(encoding='utf-8', errors='replace')
    return r.stdout

def image_fingerprint(image):
    small = ImageOps.grayscale(image.resize((8, 8)))
    pixels = image_pixels(small)
    avg = sum(pixels) / max(1, len(pixels))
    bits = ''.join('1' if p >= avg else '0' for p in pixels)
    return f'{int(bits, 2):016x}'

def colorfulness_score(pixels):
    if not pixels:
        return 0.0
    rs = [p[0] for p in pixels]
    gs = [p[1] for p in pixels]
    bs = [p[2] for p in pixels]
    mean_r = sum(rs) / len(rs)
    mean_g = sum(gs) / len(gs)
    mean_b = sum(bs) / len(bs)
    var = 0.0
    for r, g, b in pixels:
        var += abs(r - mean_r) + abs(g - mean_g) + abs(b - mean_b)
    return var / max(1, len(pixels))

def extract_sticker_messages(img, text_messages, a, page):
    w, h = img.size
    left, top, right, bottom = content_crop_box(img, a)
    content = img.crop((left, top, right, bottom)).convert('RGB')
    cw, ch = content.size
    strip = max(0, min(a.avatar_strip, cw // 4))
    scale = 4
    sw, sh = max(1, cw // scale), max(1, ch // scale)
    small = content.resize((sw, sh))
    pix = small.load()
    mask = [[False] * sw for _ in range(sh)]
    for y in range(sh):
        oy = y * scale + top
        if oy < top + a.edge_margin or oy > bottom - a.edge_margin:
            continue
        for x in range(sw):
            ox = x * scale
            if ox < strip or ox > cw - strip:
                continue
            r, g, b = pix[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            sat = mx - mn
            dark = mx < 120 and mn < 105
            is_wechat_green = g > 175 and r > 110 and b > 110 and g - r > 20 and g - b > 20
            is_soft_gray = abs(r - g) < 12 and abs(g - b) < 12 and 215 <= mx <= 245
            colorful = sat > 55 and 35 < mx < 245 and not is_wechat_green and not is_soft_gray
            if colorful or dark:
                mask[y][x] = True

    seen = [[False] * sw for _ in range(sh)]
    items = []
    text_boxes = [(m.get('left', 0), m.get('top', 0), m.get('right', 0), m.get('bottom', 0)) for m in text_messages]
    for sy in range(sh):
        for sx in range(sw):
            if not mask[sy][sx] or seen[sy][sx]:
                continue
            stack = [(sx, sy)]
            seen[sy][sx] = True
            pts = []
            while stack:
                x, y = stack.pop()
                pts.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < sw and 0 <= ny < sh and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if len(pts) < 70:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bx1, by1, bx2, by2 = min(xs) * scale, min(ys) * scale, (max(xs) + 1) * scale, (max(ys) + 1) * scale
            bw, bh = bx2 - bx1, by2 - by1
            if bw < 45 or bh < 45 or bw > cw * 0.65 or bh > ch * 0.55:
                continue
            original_crop = content.crop((max(0, bx1), max(0, by1), min(cw, bx2), min(ch, by2)))
            crop = original_crop.resize((32, 32))
            samples = image_pixels(crop)
            if colorfulness_score(samples) < 42:
                continue
            abs_box = (left + bx1, top + by1, left + bx2, top + by2)
            overlaps_text = False
            for tx1, ty1, tx2, ty2 in text_boxes:
                ix1, iy1 = max(abs_box[0], tx1 - 8), max(abs_box[1], ty1 - 8)
                ix2, iy2 = min(abs_box[2], tx2 + 8), min(abs_box[3], ty2 + 8)
                if ix2 > ix1 and iy2 > iy1:
                    overlap = (ix2 - ix1) * (iy2 - iy1)
                    if overlap > min((abs_box[2] - abs_box[0]) * (abs_box[3] - abs_box[1]), max(1, (tx2 - tx1) * (ty2 - ty1))) * 0.08:
                        overlaps_text = True
                        break
            if overlaps_text:
                continue
            center_x = (abs_box[0] + abs_box[2]) / 2
            speaker = '头像1' if center_x < (left + right) * 0.5 else '头像2'
            items.append({'line': f'{speaker}：表情', 'speaker': speaker, 'text': '表情', 'top': abs_box[1], 'bottom': abs_box[3], 'left': abs_box[0], 'right': abs_box[2], 'page': page, 'kind': 'sticker', 'fingerprint': image_fingerprint(original_crop)})

    items.sort(key=lambda x: (x['top'], x['left']))
    filtered = []
    for item in items:
        if filtered and item['speaker'] == filtered[-1]['speaker'] and abs(item['top'] - filtered[-1]['top']) < 35:
            continue
        filtered.append(item)
    return filtered

def extract_visual_card_messages(img, text_messages, a, page):
    w, h = img.size
    left, top, right, bottom = content_crop_box(img, a)
    content = img.crop((left, top, right, bottom)).convert('RGB')
    cw, ch = content.size
    scale = 5
    sw, sh = max(1, cw // scale), max(1, ch // scale)
    small = content.resize((sw, sh))
    pix = small.load()
    mask = [[False] * sw for _ in range(sh)]
    kinds = [[None] * sw for _ in range(sh)]
    for y in range(sh):
        oy = y * scale + top
        if oy < top + a.edge_margin or oy > bottom - a.edge_margin:
            continue
        for x in range(sw):
            ox = x * scale
            r, g, b = pix[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            sat = mx - mn
            is_red_packet = r > 220 and 115 < g < 185 and b < 90 and sat > 80
            is_transfer = r > 220 and 165 < g < 225 and 90 < b < 190 and sat > 35
            is_mini_program_media = b > 160 and g > 80 and r < 120 and sat > 80
            is_wechat_green = g > 175 and r > 110 and b > 110 and g - r > 20 and g - b > 20
            is_generic_media = sat > 65 and 55 < mx < 248 and not is_wechat_green
            if is_red_packet or is_transfer or is_mini_program_media or is_generic_media:
                mask[y][x] = True
                if is_red_packet:
                    kinds[y][x] = 'red_packet'
                elif is_transfer:
                    kinds[y][x] = 'transfer'
                elif is_mini_program_media:
                    kinds[y][x] = 'mini_program'
                else:
                    kinds[y][x] = 'image'

    seen = [[False] * sw for _ in range(sh)]
    cards = []
    for sy in range(sh):
        for sx in range(sw):
            if not mask[sy][sx] or seen[sy][sx]:
                continue
            stack = [(sx, sy)]
            seen[sy][sx] = True
            pts = []
            counts = {}
            while stack:
                x, y = stack.pop()
                pts.append((x, y))
                kind = kinds[y][x]
                counts[kind] = counts.get(kind, 0) + 1
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < sw and 0 <= ny < sh and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if len(pts) < 180:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bx1, by1 = min(xs) * scale, min(ys) * scale
            bx2, by2 = (max(xs) + 1) * scale, (max(ys) + 1) * scale
            bw, bh = bx2 - bx1, by2 - by1
            if bw < 150 or bh < 80:
                continue
            abs_box = (left + bx1, top + by1, left + bx2, top + by2)
            center_x = (abs_box[0] + abs_box[2]) / 2
            speaker = '头像1' if center_x < (left + right) * 0.5 else '头像2'
            kind = max(counts, key=counts.get)
            nearby_text = ''.join(
                m.get('text', '')
                for m in text_messages
                if m.get('speaker') == speaker
                and abs_box[0] - 120 <= m.get('left', 0) <= abs_box[2] + 120
                and abs_box[1] - 180 <= m.get('top', 0) <= abs_box[3] + 120
            )
            if kind == 'image' and '小程序' in nearby_text:
                kind = 'mini_program'
            text = {'red_packet': '红包', 'transfer': '转账消息', 'mini_program': '小程序卡片', 'image': '图片消息'}.get(kind, '图片消息')
            cards.append({
                'line': f'{speaker}：{text}',
                'speaker': speaker,
                'text': text,
                'top': abs_box[1],
                'bottom': abs_box[3],
                'left': abs_box[0],
                'right': abs_box[2],
                'page': page,
                'kind': kind,
            })

    filtered = []
    for card in cards:
        duplicate = False
        for old in filtered:
            if old.get('speaker') != card.get('speaker') or old.get('kind') != card.get('kind'):
                continue
            if line_overlap_ratio(old, card) >= 0.45 or abs(old.get('top', 0) - card.get('top', 0)) < 60:
                duplicate = True
                break
        if not duplicate:
            filtered.append(card)
    return filtered

def media_text_overlap(media, message, pad_x=80, pad_top=170, pad_bottom=90):
    if message.get('kind') == 'time' or message.get('speaker') == '系统':
        return False
    if media.get('speaker') != message.get('speaker'):
        return False
    mx1 = media.get('left', 0) - pad_x
    my1 = media.get('top', 0) - pad_top
    mx2 = media.get('right', 0) + pad_x
    my2 = media.get('bottom', 0) + pad_bottom
    tx1, ty1 = message.get('left', 0), message.get('top', 0)
    tx2, ty2 = message.get('right', 0), message.get('bottom', 0)
    return min(mx2, tx2) > max(mx1, tx1) and min(my2, ty2) > max(my1, ty1)

def prefer_media_placeholders(messages, media_messages):
    if not media_messages:
        return messages
    media_kinds = {'mini_program', 'red_packet', 'image', 'sticker'}
    result = []
    for message in messages:
        if message.get('kind') in media_kinds:
            result.append(message)
            continue
        if any(media_text_overlap(media, message) for media in media_messages if media.get('kind') in media_kinds):
            continue
        result.append(message)
    return result

def annotate_page_evidence(img, messages, a):
    viewport = image_fingerprint(img.crop(content_crop_box(img, a)))
    separators = '|'.join(message.get('time_raw', message.get('text', '')) for message in messages
                          if message.get('kind') == 'time' and not message.get('time_rejected'))
    for message in messages:
        if message.get('kind') == 'time' and message.get('time_rejected'):
            message['kind'] = 'non_chat_time'
            message['line'] = f"非聊天时间：{message.get('text', '')}"
        if message.get('bubble_bbox'):
            message['bubble_digest'] = hashlib.sha256(img.crop(message['bubble_bbox']).convert('RGB').tobytes()).hexdigest()
            message['viewport_signature'] = viewport
            message['separator_signature'] = separators
    return messages


def ocr_page(img, page, shots, a, tesseract_exe):
    if not tesseract_exe:
        # Windows OCR fallback has no coordinates, so keep it as raw text only.
        raw = ocr_windows(shots / f'page_{page:03d}.png', a.windows_ocr_lang)
        messages = [{'line': line, 'speaker': '', 'text': line, 'top': 0, 'bottom': 0, 'page': page} for line in clean(raw)]
        return raw, messages

    candidate_lines = []
    for threshold in parse_ocr_thresholds(a):
        ocr_img, scale, offset = prepare_text_image(img, page, shots, a, threshold, f'text_{threshold}')
        tsv = run_ocr_tsv(ocr_img, a, tesseract_exe)
        candidate_lines.extend(
            line for line in tsv_lines(tsv, scale, offset, img.size[0], img.size[1], a)
            if line.get('speaker') != '系统'
        )
    bubbles = detect_text_bubbles(img, a)
    candidate_lines = assign_lines_to_bubbles(candidate_lines, bubbles, page)
    lines = merge_ocr_line_candidates(candidate_lines)
    lines = classify_auxiliary_lines(img, lines, bubbles)
    lines = recover_unread_bubbles(img, lines, bubbles, a, tesseract_exe, page, shots)
    time_img, time_scale, time_offset = prepare_time_image(img, page, shots, a)
    time_tsv = run_ocr_tsv(time_img, a, tesseract_exe)
    time_lines = tsv_time_lines(time_tsv, time_scale, time_offset, img.size[1], a, page)
    time_lines = enrich_page_time_lines(time_lines)
    panel_left, _top, panel_right, _bottom = content_crop_box(img, a)
    panel_center = (panel_left + panel_right) / 2
    for line in time_lines:
        center = (line.get('left', 0) + line.get('right', 0)) / 2
        if abs(center - panel_center) > (panel_right - panel_left) * 0.12:
            line['time_rejected'] = '时间文字偏离聊天中线，疑似媒体时长或气泡正文'
    if time_lines:
        lines = sorted(lines + time_lines, key=lambda item: (item.get('top', 0), item.get('left', 0)))
    messages = merge_message_lines(lines, a, page)
    messages = refine_short_text_messages(img, messages, a, tesseract_exe, page)
    visual_cards = extract_visual_card_messages(img, messages, a, page)
    if visual_cards:
        messages = prefer_media_placeholders(messages, visual_cards)
        messages = sorted(messages + visual_cards, key=lambda m: (m.get('top', 0), m.get('left', 0)))
    sticker_messages = extract_sticker_messages(img, messages, a, page)
    if sticker_messages:
        messages = sorted(messages + sticker_messages, key=lambda m: (m.get('top', 0), m.get('left', 0)))
    messages = annotate_page_evidence(img, messages, a)
    raw = '\n'.join(m['line'] for m in messages)
    (shots / f'page_{page:03d}_messages.txt').write_text(raw, encoding='utf-8')
    return raw, messages




def make_calibration_image():
    EXPORTS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    img = ImageGrab.grab()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    step = 100
    for x in range(0, w, step):
        draw.line((x, 0, x, h), fill=(255, 0, 0), width=1)
        draw.text((x + 3, 3), str(x), fill=(255, 0, 0))
    for y in range(0, h, step):
        draw.line((0, y, w, y), fill=(255, 0, 0), width=1)
        draw.text((3, y + 3), str(y), fill=(255, 0, 0))
    out = EXPORTS / f'calibration_{stamp}.png'
    img.save(out)
    print('校准图已保存：', out)
    print('打开这张图，告诉我聊天区域左上角和右下角大概坐标，或用 --region left,top,right,bottom 运行。')

def choose_region(region_text=''):
    if region_text:
        nums = [int(x.strip()) for x in region_text.split(',')]
        if len(nums) != 4:
            raise RuntimeError('--region 格式应为 left,top,right,bottom')
        left, top, right, bottom = nums
    else:
        print('现在改用固定区域截图。')
        input('准备选择左上角：按回车后 3 秒内把鼠标移到微信聊天内容区域左上角，不要点击...')
        for i in range(3, 0, -1):
            print(i)
            time.sleep(1)
        left, top = cursor_pos()
        print('左上角坐标：', (left, top))
        input('准备选择右下角：按回车后 3 秒内把鼠标移到微信聊天内容区域右下角，不要点击...')
        for i in range(3, 0, -1):
            print(i)
            time.sleep(1)
        right, bottom = cursor_pos()
        print('右下角坐标：', (right, bottom))
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    if right - left < 100 or bottom - top < 100:
        raise RuntimeError('选择区域太小，请重新选择聊天内容区域。')
    return (left, top, right, bottom)

def move_mouse_to_region_center(box):
    x = int((box[0] + box[2]) / 2)
    y = int((box[1] + box[3]) / 2)
    user32.SetCursorPos(x, y)

def focus_region_once(box, x_ratio=0.5, y_ratio=0.45):
    x = int(box[0] + (box[2] - box[0]) * x_ratio)
    y = int(box[1] + (box[3] - box[1]) * y_ratio)
    user32.SetCursorPos(x, y)
    time.sleep(0.12)

def compute_scroll_plan(box, a):
    if a.scroll:
        pulse = a.scroll
        return pulse, 1
    height = max(1, box[3] - box[1])
    target_pixels = height * max(0.1, min(0.9, a.scroll_ratio))
    total_notches = max(10, int(target_pixels / max(1.0, a.wheel_pixels_per_notch)))
    pulse = -max(1, abs(a.scroll_pulse))
    count = max(1, int((total_notches + abs(pulse) - 1) / abs(pulse)))
    return pulse, count

def scroll_region(box, pulse, count=1, pulse_delay=0.04, x_ratio=0.5, y_ratio=0.45):
    x = int(box[0] + (box[2] - box[0]) * x_ratio)
    y = int(box[1] + (box[3] - box[1]) * y_ratio)
    user32.SetCursorPos(x, y)
    time.sleep(0.03)
    for _ in range(max(1, count)):
        user32.mouse_event(0x0800, 0, 0, pulse * 120, 0)
        time.sleep(max(0.0, pulse_delay))

def looks_like_console_capture(img):
    small = ImageOps.grayscale(img.resize((80, 80)))
    pixels = image_pixels(small)
    dark_ratio = sum(1 for p in pixels if p < 40) / max(1, len(pixels))
    return dark_ratio > 0.55

def clean(text):
    skip = {'微信', '发送', '按住说话', '聊天信息'}
    out = []
    for line in text.splitlines():
        line = re.sub(r'\s+', ' ', line.strip())
        if line and line not in skip:
            out.append(line)
    return out

def split_speaker_text(line):
    if '：' in line:
        speaker, text = line.split('：', 1)
        return speaker, text
    return '', line

def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}

def save_config(config):
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

def resolve_chat_profile(a, title=''):
    config = load_config()
    target_name = a.target_name or config.get('target_name') or title or '对方'
    self_position = a.self_position or config.get('self_position') or ''
    if self_position not in ('left', 'right') and not a.no_config_prompt:
        print('首次结构化导出需要确认聊天窗口布局。')
        print('1. 左侧是我')
        print('2. 右侧是我')
        choice = input('请选择 1 或 2，直接回车默认右侧是我：').strip()
        self_position = 'left' if choice == '1' else 'right'
    self_position = self_position if self_position in ('left', 'right') else 'right'
    if (not a.target_name) and (not config.get('target_name')) and not a.no_config_prompt:
        value = input(f'聊天对象名称 [{target_name}]：').strip()
        if value:
            target_name = value
    profile = {
        'target_name': target_name,
        'self_position': self_position,
        'other_position': 'left' if self_position == 'right' else 'right',
    }
    config['target_name'] = target_name
    config['self_position'] = self_position
    save_config(config)
    return profile

def parse_message_time(text, context):
    text = re.sub(r'(\d{1,2}\s*月\s*\d{1,2})\s+(\d{1,2}:\d{2})', r'\1日\2', text or '')
    compact = re.sub(r'\s+', '', text).strip('-—_.，,。:：|丨')
    compact = re.sub(r'(?<=\d)[.·](?=\d{2}$)', ':', compact)
    if not compact:
        return None
    base = context.get('last_datetime') or context.get('run_datetime') or dt.datetime.now()
    m = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:(\d{1,2}):(\d{2}))?', compact)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        try:
            return dt.datetime(year, month, day, hour, minute)
        except ValueError:
            return None
    m = re.fullmatch(r'(\d{1,2})月(\d{1,2})(?:日|号)(?:(\d{1,2}):(\d{2}))?', compact)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        hour = int(m.group(3) or 0)
        minute = int(m.group(4) or 0)
        try:
            return dt.datetime(base.year, month, day, hour, minute)
        except ValueError:
            return None
    m = re.fullmatch(r'(今天|昨天|前天)?(\d{1,2}):(\d{2})', compact)
    if m:
        label = m.group(1)
        if label in ('今天', '昨天', '前天'):
            offset = {'今天': 0, '昨天': 1, '前天': 2}.get(label, 0)
            day_base = context.get('relative_date_anchor')
            if day_base is None:
                return None
            day = day_base - dt.timedelta(days=offset)
        else:
            day = context.get('last_datetime')
            if day is None:
                return None
        try:
            return dt.datetime(day.year, day.month, day.day, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.fullmatch(r'星期([一二三四五六日天])(\d{1,2}):(\d{2})', compact)
    if m:
        weekday_map = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
        day_base = context.get('relative_date_anchor')
        if day_base is None:
            return None
        target = weekday_map.get(m.group(1), day_base.weekday())
        days_back = (day_base.weekday() - target) % 7
        try:
            return dt.datetime(day_base.year, day_base.month, day_base.day, int(m.group(2)), int(m.group(3))) - dt.timedelta(days=days_back)
        except ValueError:
            return None
    return None

def format_message_time(value):
    return value.strftime('%Y-%m-%d %H:%M') if isinstance(value, dt.datetime) else ''

def message_side(speaker):
    if speaker == '头像1':
        return 'left'
    if speaker == '头像2':
        return 'right'
    return 'system'

def sender_from_side(side, profile):
    if side == profile.get('self_position'):
        return '我'
    if side in ('left', 'right'):
        return profile.get('target_name') or '对方'
    return '系统'

def infer_message_type(message):
    text = message.get('text', '')
    if message.get('kind') in ('recall', 'quoted_context', 'unread_text', 'non_chat_time'):
        return message['kind']
    if message.get('kind') in ('red_packet', 'transfer', 'mini_program', 'image'):
        return message.get('kind')
    if message.get('kind') == 'sticker' or text == '表情':
        return 'sticker'
    compact = re.sub(r'\s+', '', text)
    if '通话时长' in compact:
        return 'call'
    if compact in ('审批',):
        return 'card'
    if compact in ('红包', '微信红包') or '领取了你的红包' in compact:
        return 'red_packet'
    if compact in ('小程序', '小程序卡片'):
        return 'mini_program'
    if not message.get('bubble_id') and ('撤回了一条消息' in text or '撤回消息' in text):
        return 'recall'
    if '语音' in text and len(text) <= 12:
        return 'voice'
    if '文件' in text and len(text) <= 20:
        return 'file'
    if text in ('图片', '图片消息'):
        return 'image'
    return 'text'

def placeholder_content(msg_type, content):
    placeholders = {
        'sticker': '[表情]',
        'image': '[图片消息]',
        'voice': '[语音消息]',
        'file': '[文件消息]',
        'recall': '[撤回消息]',
        'card': '[卡片消息]',
        'red_packet': '[红包]',
        'transfer': '[转账消息]',
        'mini_program': '[小程序卡片]',
        'call': '[通话记录]',
        'unknown': '[未知消息]',
    }
    return placeholders.get(msg_type, content)

def consume_time_line(line, context):
    if line.get('kind') == 'non_chat_time':
        return
    parsed = parse_message_time(line.get('text', ''), context)
    context['time_raw'] = line.get('time_raw', line.get('text', ''))
    context['time_source'] = f'page_{int(line.get("page", 0)):03d}.png'
    context['time_marker'] = f'{line.get("page", 0)}:{line.get("top", 0)}:{line.get("text", "")}'
    context['time_original'] = format_message_time(parsed)
    context['time_reason'] = line.get('time_rejected', '') or ('' if parsed else '时间节点无法可靠解析')
    if not context['time_reason']:
        context['time_reason'] = time_anomaly_reason(context.get('last_datetime'), parsed, None)
    context['current_datetime'] = None if context['time_reason'] else parsed
    if not context['time_reason']:
        context['last_datetime'] = parsed


def build_structured_message(message, profile, current_time, source_image, time_context=None):
    side = message_side(message.get('speaker', ''))
    msg_type = infer_message_type(message)
    content = message.get('text', '').strip()
    record = {
        'time': format_message_time(current_time),
        'sender': sender_from_side(side, profile),
        'side': side,
        'type': msg_type,
        'content': content,
        'source_image': str(source_image),
        'page': int(message.get('page', 0)),
        'bbox': [
            int(message.get('left', 0)),
            int(message.get('top', 0)),
            int(message.get('right', 0)),
            int(message.get('bottom', 0)),
        ],
    }
    for key in ('bubble_id', 'bubble_bbox', 'bubble_complete', 'bubble_digest', 'viewport_signature',
                'separator_signature', 'sender_evidence', 'structure_warning', 'structure_category'):
        if message.get(key) is not None:
            record[key] = message[key]
    if time_context:
        for key in ('time_raw', 'time_source', 'time_marker', 'time_original', 'time_reason'):
            record[key] = time_context.get(key, '')
        if record.get('time_reason'):
            record['time_status'] = 'pending'
            record['time'] = ''
    return record

def parse_record_time(record):
    if record.get('time_status') == 'pending':
        return None
    value = record.get('time')
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, '%Y-%m-%d %H:%M')
    except ValueError:
        return None

def compute_export_stats(records):
    total = len(records)
    by_sender = {}
    by_type = {}
    for record in records:
        by_sender[record['sender']] = by_sender.get(record['sender'], 0) + 1
        by_type[record['type']] = by_type.get(record['type'], 0) + 1
    times = [parse_record_time(record) for record in records]
    times = [item for item in times if item]
    starts = {}
    last_time = None
    for record in records:
        current = parse_record_time(record)
        if current and (last_time is None or (current - last_time).total_seconds() >= 6 * 3600):
            starts[record['sender']] = starts.get(record['sender'], 0) + 1
        if current:
            last_time = current
    return {
        'chat_period': {
            'start': min(times).strftime('%Y-%m-%d %H:%M') if times else '',
            'end': max(times).strftime('%Y-%m-%d %H:%M') if times else '',
        },
        'total_messages': total,
        'by_sender': by_sender,
        'sender_ratio': {k: round(v / total, 4) for k, v in by_sender.items()} if total else {},
        'by_type': by_type,
        'active_starts': starts,
        'pending_time_messages': sum(record.get('time_status') == 'pending' for record in records),
    }

def export_markdown(path, records, profile, stats):
    out = [f'# 与{profile.get("target_name", "对方")}聊天记录', '']
    out.extend(['## 聊天统计', ''])
    period = stats.get('chat_period', {})
    out.append(f'- 聊天周期：{period.get("start", "")} - {period.get("end", "")}')
    out.append(f'- 总消息：{stats.get("total_messages", 0)} 条')
    if stats.get('pending_time_messages'):
        out.append(f'- 时间待核对：{stats["pending_time_messages"]} 条，未计入聊天周期')
    for sender, count in stats.get('by_sender', {}).items():
        ratio = stats.get('sender_ratio', {}).get(sender, 0)
        out.append(f'- {sender}发送：{count} 条，占比 {ratio:.1%}')
    out.append('')
    last_date = ''
    last_clock = ''
    last_time_note = ''
    for record in records:
        t = parse_record_time(record)
        date_label = t.strftime('%Y-%m-%d') if t else ('时间待核对' if record.get('time_status') == 'pending' else '时间未识别')
        clock_label = t.strftime('%H:%M') if t else ''
        if date_label != last_date:
            out.extend(['', f'## {date_label}', ''])
            last_date = date_label
            last_clock = ''
        if clock_label and clock_label != last_clock:
            out.extend([f'### {clock_label}', ''])
            last_clock = clock_label
        if record.get('time_status') == 'pending' and (record.get('time_raw') or record.get('time_original')):
            time_note = f'> 时间待核对；OCR 原文：{record.get("time_raw") or record.get("time_original")}；来源：{record.get("time_source") or source_image_label(record)}'
            if time_note != last_time_note:
                out.extend([time_note, ''])
            last_time_note = time_note
        else:
            last_time_note = ''
        content = placeholder_content(record.get('type'), record.get('content', ''))
        out.extend([f'[{record.get("sender", "")}]', content, ''])
    path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')

def write_json(path, records, profile, stats):
    payload = {
        'target_name': profile.get('target_name'),
        'self_position': profile.get('self_position'),
        'stats': stats,
        'messages': records,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

def report_record_label(record, index):
    time_text = record.get('time') or ('时间待核对' if record.get('time_status') == 'pending' else '时间未识别')
    sender = record.get('sender') or ''
    page = record.get('page') or ''
    return f'第 {index} 条，页 {page}，{time_text}，{sender}'

def source_image_label(record):
    source = record.get('source_image') or ''
    return Path(source).name if source else ''

def normalize_dedupe_text(text):
    text = clean_message_text(text or '')
    text = text.strip().lower()
    replacements = {
        ',': '，',
        '.': '。',
        '?': '？',
        '!': '！',
        ':': '：',
        ';': '；',
        '(': '（',
        ')': '）',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', text)
    text = re.sub(r'[\s\|丨\\/_=~`^{}\\[\\]<>]+$', '', text)
    text = re.sub(r'[0-9０-９]{1,2}$', '', text).strip()
    return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]+', '', text)

def record_text_quality_score(record):
    text = record.get('content', '') or ''
    norm = normalize_dedupe_text(text)
    score = len(norm)
    score += len(re.findall(r'[\u4e00-\u9fff]', text)) * 2
    score -= len(re.findall(r'[|丨\\/_=~`^{}\\[\\]<>]', text)) * 5
    if re.search(r'[。？！?!]$', text.strip()):
        score += 4
    bbox = record_bbox(record)
    if bbox[3] > bbox[1]:
        score += min(12, int((bbox[3] - bbox[1]) / 18))
    return score

def record_similarity_score(a_record, b_record):
    a = normalize_dedupe_text(a_record.get('content', ''))
    b = normalize_dedupe_text(b_record.get('content', ''))
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()

def sequence_match_coverage(a, b):
    if not a or not b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, a, b)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / max(1, min(len(a), len(b)))

def page_distance(a_record, b_record):
    return abs(int(a_record.get('page') or 0) - int(b_record.get('page') or 0))

def time_distance_seconds(a_record, b_record):
    a_time = parse_record_time(a_record)
    b_time = parse_record_time(b_record)
    if not a_time or not b_time:
        return None
    return abs((a_time - b_time).total_seconds())

def near_cross_page_edge(a_record, b_record, bounds):
    a_page = int(a_record.get('page') or 0)
    b_page = int(b_record.get('page') or 0)
    if a_page == b_page:
        return False
    ax1, ay1, ax2, ay2 = record_bbox(a_record)
    bx1, by1, bx2, by2 = record_bbox(b_record)
    a_top, a_bottom = bounds.get(a_page, (0, 0))
    b_top, b_bottom = bounds.get(b_page, (0, 0))
    a_near_bottom = a_bottom and ay1 >= a_bottom - 340
    b_near_top = b_top and by2 <= b_top + 340
    b_near_bottom = b_bottom and by1 >= b_bottom - 340
    a_near_top = a_top and ay2 <= a_top + 340
    overlap = horizontal_overlap_ratio((ax1, ay1, ax2, ay2), (bx1, by1, bx2, by2)) >= 0.45
    return overlap and ((a_page < b_page and a_near_bottom and b_near_top) or (b_page < a_page and b_near_bottom and a_near_top))

def duplicate_relation(a_record, b_record, bounds):
    if a_record.get('type') != 'text' or b_record.get('type') != 'text':
        return None
    if page_distance(a_record, b_record) > 2:
        return None
    if (page_distance(a_record, b_record) == 0 and a_record.get('bubble_id')
            and b_record.get('bubble_id') and a_record['bubble_id'] != b_record['bubble_id']):
        return None
    a = normalize_dedupe_text(a_record.get('content', ''))
    b = normalize_dedupe_text(b_record.get('content', ''))
    if not a or not b:
        return None
    if (page_distance(a_record, b_record) == 1 and a == b
            and a_record.get('sender') == b_record.get('sender')
            and a_record.get('bubble_digest') and a_record['bubble_digest'] == b_record.get('bubble_digest')
            and a_record.get('bubble_bbox') == b_record.get('bubble_bbox')
            and a_record.get('viewport_signature')
            and a_record.get('viewport_signature') == b_record.get('viewport_signature')
            and a_record.get('separator_signature')
            and a_record.get('separator_signature') == b_record.get('separator_signature')):
        return {'similarity': 1.0, 'reason': '相邻截图视口、时间分隔符及气泡像素一致，同一消息被再次识别',
                'auto_remove': True}
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    a_bubble, b_bubble = a_record.get('bubble_bbox'), b_record.get('bubble_bbox')
    aligned_bubbles = (a_bubble and b_bubble
                       and abs(a_bubble[0] - b_bubble[0]) <= 12
                       and abs(a_bubble[2] - b_bubble[2]) <= 12)
    same_context = (a_record.get('time') and a_record.get('time') == b_record.get('time')) or (
        a_record.get('time_raw') and a_record.get('time_raw') == b_record.get('time_raw'))
    short_issue = None
    if (3 <= len(shorter) < 14 and page_distance(a_record, b_record) == 1
            and a_record.get('sender') == b_record.get('sender') and aligned_bubbles and same_context
            and difflib.SequenceMatcher(None, a, b).ratio() >= 0.65):
        short_issue = {'similarity': difflib.SequenceMatcher(None, a, b).ratio(),
                       'reason': '相邻截图同侧气泡位置与时间上下文吻合，短句存在 OCR 差异；仅提示核对，不自动删除',
                       'auto_remove': False}
    if len(shorter) < 6:
        return short_issue
    sim = difflib.SequenceMatcher(None, a, b).ratio()
    coverage = sequence_match_coverage(a, b)
    containment = shorter in longer
    containment_ratio = len(shorter) / max(1, len(longer))
    time_gap = time_distance_seconds(a_record, b_record)
    time_ok = time_gap is None or time_gap <= 15 * 60
    edge_ok = near_cross_page_edge(a_record, b_record, bounds)
    page_close = page_distance(a_record, b_record) <= 1
    same_sender = a_record.get('sender') == b_record.get('sender')
    same_side = a_record.get('side') and a_record.get('side') == b_record.get('side')
    auto_safe_sender = same_sender or same_side
    if containment and containment_ratio >= 0.50 and (time_ok or edge_ok) and auto_safe_sender:
        return {'similarity': containment_ratio, 'reason': '一条文本是另一条的截断/包含版本', 'auto_remove': True}
    if sim >= 0.88 and len(shorter) >= 8 and (time_ok or edge_ok or page_close) and auto_safe_sender:
        return {'similarity': sim, 'reason': '邻近截图中文本高度相似', 'auto_remove': True}
    if sim >= 0.82 and len(shorter) >= 14 and edge_ok and auto_safe_sender:
        return {'similarity': sim, 'reason': '跨页边缘文本高度相似', 'auto_remove': True}
    if coverage >= 0.78 and len(shorter) >= 14 and (time_ok or edge_ok) and auto_safe_sender:
        return {'similarity': max(sim, coverage), 'reason': '邻近截图长文本匹配覆盖率高，疑似 OCR 差异版重复', 'auto_remove': True}
    if (page_distance(a_record, b_record) == 1 and same_sender and time_gap == 0
            and aligned_bubbles and len(shorter) >= 8 and len(longer) >= 30
            and len(shorter) / len(longer) < 0.50 and coverage >= 0.85):
        return {'similarity': coverage, 'reason': '相邻截图的同侧气泡横向边界一致、时间相同，短文本疑似长消息残段；仅提示核对',
                'auto_remove': False}
    if not same_sender:
        if containment and containment_ratio >= 0.62 and len(shorter) >= 10 and (time_ok or edge_ok):
            return {'similarity': containment_ratio, 'reason': 'sender 不同，但一条文本高度疑似为另一条截断版本；建议人工核对', 'auto_remove': False}
        if sim >= 0.90 and len(shorter) >= 12 and (time_ok or edge_ok):
            return {'similarity': sim, 'reason': 'sender 不同，但邻近截图文本高度相似；建议人工核对', 'auto_remove': False}
        if coverage >= 0.72 and len(shorter) >= 14 and (time_ok or edge_ok):
            return {'similarity': max(sim, coverage), 'reason': 'sender 不同，但长文本匹配覆盖率较高；仅提示人工核对，不自动删除', 'auto_remove': False}
    return short_issue

def preferred_duplicate_record(a_record, b_record):
    a_score = record_text_quality_score(a_record)
    b_score = record_text_quality_score(b_record)
    if abs(a_score - b_score) <= 2:
        a_len = len(normalize_dedupe_text(a_record.get('content', '')))
        b_len = len(normalize_dedupe_text(b_record.get('content', '')))
        if b_len > a_len:
            return b_record, a_record
        return a_record, b_record
    if b_score > a_score:
        return b_record, a_record
    return a_record, b_record

def fuzzy_dedupe_records(records):
    bounds = page_vertical_bounds(records)
    kept = []
    duplicate_issues = []
    for original_index, record in enumerate(records, 1):
        candidate = dict(record)
        candidate['_original_index'] = original_index
        duplicate_index = None
        duplicate_info = None
        for idx in range(len(kept) - 1, max(-1, len(kept) - 18), -1):
            info = duplicate_relation(kept[idx], candidate, bounds)
            if info:
                duplicate_index = idx
                duplicate_info = info
                break
        if duplicate_index is None:
            kept.append(candidate)
            continue
        old = kept[duplicate_index]
        preferred, removed = preferred_duplicate_record(old, candidate)
        auto_remove = bool(duplicate_info.get('auto_remove', True))
        if auto_remove and preferred is candidate:
            kept[duplicate_index] = candidate
        duplicate_issues.append({
            'category': '疑似重复消息',
            'first_index': old.get('_original_index'),
            'second_index': candidate.get('_original_index'),
            'first': old,
            'second': candidate,
            'preferred': preferred,
            'removed': removed,
            'similarity': round(float(duplicate_info.get('similarity', 0.0)), 3),
            'detail': duplicate_info.get('reason', ''),
            'auto_removed': auto_remove,
        })
        if not auto_remove:
            kept.append(candidate)
    for record in kept:
        record.pop('_original_index', None)
    return kept, duplicate_issues

def add_report_issue(issues, category, record, index, detail):
    issues.setdefault(category, []).append({
        'label': report_record_label(record, index),
        'content': placeholder_content(record.get('type'), record.get('content', '')),
        'source': source_image_label(record),
        'detail': detail,
    })

def suspicious_ocr_reasons(record):
    if record.get('type') != 'text':
        return []
    text = record.get('content', '').strip()
    compact = re.sub(r'\s+', '', text)
    reasons = []
    if not compact:
        reasons.append('空文本')
    if re.search(r'[\u4e00-\u9fff]\s*[0-9０-９]\s*[\u4e00-\u9fff]', text):
        reasons.append('中文句子中夹有孤立数字')
    if re.search(r'[\u4e00-\u9fff]\s+[0-9０-９]{1,2}$', text):
        reasons.append('中文句子末尾疑似残留数字')
    if re.search(r'[\u4e00-\u9fff]\s*[-–—]\s*[\u4e00-\u9fff]', text):
        reasons.append('中文句子中夹有横线')
    if re.search(r'[|丨\\\\/_=~`^{}\\[\\]<>]', text):
        reasons.append('含常见 OCR 噪声符号')
    if re.search(r'[A-Za-z]{2,}', text) and re.search(r'[\u4e00-\u9fff]', text):
        reasons.append('中文句子中夹有连续英文字母')
    if re.search(r'\b[A-Z]{2,4}$', text):
        reasons.append('末尾疑似残留英文字母')
    if len(compact) >= 4 and compact.endswith('人'):
        reasons.append('末尾为“人”，可能是头像/边缘残留')
    if re.search(r'(小程序|微信红包|微信转账|腾讯访客|邀请您访问|完成身份验证|聊天记录|质量检查|导出结果)', text):
        reasons.append('疑似卡片内部文字残留')
    if len(compact) == 1:
        reasons.append('单字消息，建议核对是否真实聊天')
    return reasons

def normalized_sentence_text(text):
    text = re.sub(r'\s+', '', text or '')
    text = re.sub(r'[|丨\\/_=~`^{}\\[\\]<>]', '', text)
    text = re.sub(r'[0-9０-９]{1,2}$', '', text)
    return text

def ends_like_unfinished(text):
    value = normalized_sentence_text(text)
    if len(value) < 3:
        return False
    if re.search(r'[。？！?!…]$', value):
        return False
    unfinished_suffixes = (
        '的', '和', '但', '但是', '如果', '因为', '这个', '那个', '这', '那',
        '其', '其实', '然后', '所以', '就是', '比较', '一直', '把', '给', '跟',
        '对', '在', '从', '让', '说', '男', '女', '人家', '当时'
    )
    if value.endswith(unfinished_suffixes):
        return True
    return False

def starts_like_continuation(text):
    value = normalized_sentence_text(text)
    if len(value) < 2:
        return False
    continuation_prefixes = (
        '的', '了', '地', '得', '都', '才', '能', '可以', '一直', '比较',
        '女', '男', '跟', '给', '在', '把'
    )
    if value.startswith(continuation_prefixes):
        return True
    if re.match(r'^[，。？！?!、；;：:）)]', value):
        return True
    return False

def looks_like_continuous_pair(previous, current):
    if previous.get('type') != 'text' or current.get('type') != 'text':
        return False
    a = normalized_sentence_text(previous.get('content', ''))
    b = normalized_sentence_text(current.get('content', ''))
    if len(a) < 3 or len(b) < 2:
        return False
    # Split-word evidence is report-only; it never changes sender or content.
    if len(a) >= 18 and not re.search(r'[。？！?!…]$', a):
        if a[-1] + b[0] in ('社会', '想法', '之前', '之后', '时候', '事情', '任务', '意义', '努力', '主动', '关系', '结婚'):
            return True
    if not ends_like_unfinished(a):
        return False
    if starts_like_continuation(b):
        return True
    return False

def pair_time_close(previous, current):
    gap = time_distance_seconds(previous, current)
    if gap is None:
        return True
    return gap <= 10 * 60

def pair_page_close(previous, current):
    return page_distance(previous, current) <= 1

def near_pair_edges(previous, current, bounds):
    return near_cross_page_edge(previous, current, bounds)

def add_pair_issue(issues, category, previous, current, previous_index, current_index, detail):
    issues.setdefault(category, []).append({
        'pair': True,
        'label': f'第 {previous_index}-{current_index} 条疑似结构异常',
        'previous_label': report_record_label(previous, previous_index),
        'current_label': report_record_label(current, current_index),
        'previous_sender': previous.get('sender', ''),
        'current_sender': current.get('sender', ''),
        'previous_content': placeholder_content(previous.get('type'), previous.get('content', '')),
        'current_content': placeholder_content(current.get('type'), current.get('content', '')),
        'previous_source': source_image_label(previous),
        'current_source': source_image_label(current),
        'detail': detail,
    })

def add_duplicate_issue(issues, item):
    preferred = item.get('preferred') or {}
    first = item.get('first') or {}
    second = item.get('second') or {}
    uncertain_short = (not item.get('auto_removed', True)
                       and max(len(normalize_dedupe_text(first.get('content', ''))),
                               len(normalize_dedupe_text(second.get('content', '')))) < 14)
    issues.setdefault('疑似重复消息', []).append({
        'duplicate': True,
        'label': f"原始识别第 {item.get('first_index')}-{item.get('second_index')} 项疑似重复（去重前编号）",
        'first_label': report_record_label(first, item.get('first_index') or 0),
        'second_label': report_record_label(second, item.get('second_index') or 0),
        'first_content': placeholder_content(first.get('type'), first.get('content', '')),
        'second_content': placeholder_content(second.get('type'), second.get('content', '')),
        'first_source': source_image_label(first),
        'second_source': source_image_label(second),
        'preferred_label': ('无法可靠确定，请核对截图' if uncertain_short else
                            report_record_label(preferred, preferred.get('_original_index') or item.get('first_index') or 0)),
        'similarity': item.get('similarity', 0),
        'detail': item.get('detail', ''),
        'auto_removed': item.get('auto_removed', True),
    })

def time_anomaly_reason(previous_time, current_time, next_time):
    if not current_time:
        return ''
    if previous_time and current_time < previous_time - dt.timedelta(hours=3):
        if next_time and next_time >= previous_time - dt.timedelta(minutes=30):
            return '当前时间相对上一条大幅倒退，后一条又恢复到原时间段，疑似 OCR 时间识别错误'
        return '当前时间相对上一条大幅倒退，疑似时间顺序异常'
    if previous_time and next_time:
        before_gap = abs((current_time - previous_time).total_seconds())
        after_gap = abs((next_time - current_time).total_seconds())
        surrounding_gap = abs((next_time - previous_time).total_seconds())
        if before_gap >= 6 * 3600 and after_gap >= 6 * 3600 and surrounding_gap <= 2 * 3600:
            return '当前时间明显偏离前后时间，疑似孤立异常时间戳'
    return ''

def add_time_anomaly_issue(issues, record, index, previous_time, next_time, reason):
    issues.setdefault('疑似时间戳异常', []).append({
        'time_anomaly': True,
        'label': report_record_label(record, index),
        'current_time': record.get('time_original') or record.get('time_raw') or record.get('time') or '时间未识别',
        'raw_time': record.get('time_raw', ''),
        'previous_time': previous_time.strftime('%Y-%m-%d %H:%M') if previous_time else '',
        'next_time': next_time.strftime('%Y-%m-%d %H:%M') if next_time else '',
        'source': record.get('time_source') or source_image_label(record),
        'detail': reason,
    })

def nearest_previous_time(times, current_index, current_time=None):
    for item in reversed(times[:current_index]):
        if item and (current_time is None or item != current_time):
            return item
    return None

def nearest_next_time(times, current_index, current_time=None):
    for item in times[current_index + 1:]:
        if item and (current_time is None or item != current_time):
            return item
    return None

def first_known_datetime(records):
    for record in records:
        parsed = parse_record_time(record)
        if parsed:
            return parsed
    return dt.datetime.now()

def extract_raw_time_markers(raw_pages, records):
    context = {
        'run_datetime': first_known_datetime(records),
        'last_datetime': None,
    }
    markers = []
    for page, text in raw_pages or []:
        for line in str(text or '').splitlines():
            speaker, body = split_speaker_text(line)
            if speaker != '系统':
                continue
            parsed = parse_message_time(body, context)
            if not parsed:
                continue
            context['last_datetime'] = parsed
            page_number = int(page or 0)
            markers.append({
                'time': parsed,
                'text': body.strip(),
                'page': page_number,
                'source': f'page_{page_number:03d}.png' if page_number > 0 else '',
            })
    return markers

def add_raw_time_anomaly_issue(issues, marker, index, previous_time, next_time, reason):
    issues.setdefault('疑似时间戳异常', []).append({
        'time_anomaly': True,
        'label': f'第 {index} 个时间节点：{marker.get("text") or "时间未识别"}',
        'current_time': format_message_time(marker.get('time')) or '时间未识别',
        'previous_time': previous_time.strftime('%Y-%m-%d %H:%M') if previous_time else '',
        'next_time': next_time.strftime('%Y-%m-%d %H:%M') if next_time else '',
        'source': marker.get('source', ''),
        'detail': reason,
    })

def add_raw_time_anomalies(issues, raw_pages, records):
    markers = extract_raw_time_markers(raw_pages, records)
    marker_times = [marker.get('time') for marker in markers]
    for zero_index, marker in enumerate(markers):
        current_time = marker.get('time')
        previous_time = nearest_previous_time(marker_times, zero_index, current_time)
        next_time = nearest_next_time(marker_times, zero_index, current_time)
        reason = time_anomaly_reason(previous_time, marker.get('time'), next_time)
        if reason:
            add_raw_time_anomaly_issue(issues, marker, zero_index + 1, previous_time, next_time, reason)


def quarantine_unreliable_times(records):
    result = [dict(record) for record in records]
    nodes = []
    for record in result:
        node_key = record.get('time_marker') or record.get('time_original') or record.get('time')
        if not node_key:
            continue
        original_time = record.get('time_original') or record.get('time') or ''
        parsed = parse_record_time({'time': original_time})
        if nodes and nodes[-1]['key'] == node_key:
            nodes[-1]['records'].append(record)
        else:
            nodes.append({'key': node_key, 'time': parsed, 'records': [record]})
    times = [node['time'] for node in nodes]
    trusted_previous = None
    for index, node in enumerate(nodes):
        record = node['records'][0]
        reason = record.get('time_reason', '')
        next_time = nearest_next_time(times, index, node['time'])
        if not reason:
            reason = time_anomaly_reason(trusted_previous, node['time'], next_time)
        if reason or record.get('time_status') == 'pending':
            for item in node['records']:
                item.setdefault('time_original', item.get('time', ''))
                item['time'] = ''
                item['time_status'] = 'pending'
                item['time_reason'] = reason or '时间节点无法可靠解析'
                item['time_previous'] = format_message_time(trusted_previous)
                item['time_next'] = format_message_time(next_time)
        elif node['time']:
            trusted_previous = node['time']
    return result

def build_quality_issues(records, duplicate_issues=None, raw_pages=None):
    issues = {}
    for item in duplicate_issues or []:
        add_duplicate_issue(issues, item)
    add_raw_time_anomalies(issues, raw_pages, records)
    previous = None
    bounds = page_vertical_bounds(records)
    parsed_times = [parse_record_time(record) for record in records]
    for index, record in enumerate(records, 1):
        if record.get('structure_warning'):
            add_report_issue(issues, record.get('structure_category') or '疑似消息漏识别', record, index, record['structure_warning'])
        if record.get('time_status') == 'pending':
            add_time_anomaly_issue(issues, record, index,
                                   parse_record_time({'time': record.get('time_previous')}),
                                   parse_record_time({'time': record.get('time_next')}),
                                   record.get('time_reason') or '时间待核对，已排除出聊天周期统计')
        elif not record.get('time'):
            add_report_issue(issues, '时间未识别', record, index, '这条消息没有继承到明确时间')
        current_time = parsed_times[index - 1]
        previous_time = nearest_previous_time(parsed_times, index - 1, current_time)
        next_time = nearest_next_time(parsed_times, index - 1, current_time)
        reason = time_anomaly_reason(previous_time, current_time, next_time)
        if reason:
            add_time_anomaly_issue(issues, record, index, previous_time, next_time, reason)
        for reason in suspicious_ocr_reasons(record):
            add_report_issue(issues, '疑似 OCR 误差', record, index, reason)
        if previous:
            same_sender = previous.get('sender') == record.get('sender')
            same_content = previous.get('content') == record.get('content')
            close_page = abs(int(record.get('page') or 0) - int(previous.get('page') or 0)) <= 1
            if same_sender and same_content and close_page:
                add_report_issue(issues, '疑似重复消息', record, index, '与上一条同发送者同内容，且来源页相邻')
            if pair_time_close(previous, record) and pair_page_close(previous, record) and looks_like_continuous_pair(previous, record):
                if previous.get('sender') != record.get('sender'):
                    add_pair_issue(
                        issues,
                        '疑似发送者归属异常',
                        previous,
                        record,
                        index - 1,
                        index,
                        '上下文像同一句续接，但发送者发生变化；建议人工核对 sender 是否识别错。'
                    )
                else:
                    add_pair_issue(
                        issues,
                        '疑似消息截断',
                        previous,
                        record,
                        index - 1,
                        index,
                        '上一条明显未结束，下一条像从句中间继续；可能是同一条消息被拆开。'
                    )
            if (
                previous.get('type') == 'text'
                and record.get('type') == 'text'
                and previous.get('sender') == record.get('sender')
                and page_distance(previous, record) == 1
                and near_pair_edges(previous, record, bounds)
                and (ends_like_unfinished(previous.get('content', '')) or starts_like_continuation(record.get('content', '')))
            ):
                add_pair_issue(
                    issues,
                    '疑似跨页消息续接',
                    previous,
                    record,
                    index - 1,
                    index,
                    '两条文本位于相邻截图边缘，且语句可能连续；建议核对是否为同一条跨页消息。'
                )
        previous = record
    return issues

def export_quality_report(path, records, profile, stats, raw_pages=None, stopped_reason='', duplicate_issues=None, isolated_records=None):
    raw_pages = raw_pages or []
    issues = build_quality_issues(records, duplicate_issues, raw_pages)
    for record in isolated_records or []:
        if record.get('type') == 'non_chat_time':
            issues.setdefault('非聊天时间已忽略', []).append({
                'label': '非聊天时间节点，不改变消息的可信时间继承', 'content': record.get('content', ''),
                'source': source_image_label(record), 'detail': '时间文字偏离聊天时间分隔符区域，已忽略；原文保留供核对'})
            continue
        issues.setdefault('引用预览已隔离', []).append({
            'label': '灰色回复引用，不计为一条新的发言', 'content': record.get('content', ''),
            'source': source_image_label(record), 'detail': '气泡外浅灰文字与回复竖线对应，已从独立消息中隔离；原文保留供核对'})
    out = [f'# 与{profile.get("target_name", "对方")}聊天记录质量检查', '']
    out.extend(['## 导出概况', ''])
    out.append(f'- 消息数：{stats.get("total_messages", 0)} 条')
    out.append(f'- OCR 页数：{len(raw_pages)} 页')
    period = stats.get('chat_period', {})
    out.append(f'- 聊天周期：{period.get("start", "")} - {period.get("end", "")}')
    if issues.get('疑似时间戳异常'):
        if stats.get('pending_time_messages'):
            out.append('- 时间提示：待核对时间已排除出聊天周期统计，周期仅涵盖可信时间。')
        else:
            out.append('- 时间提示：存在疑似异常时间戳，请优先核对。')
    if stopped_reason:
        out.append(f'- 停止原因：{stopped_reason}')
    else:
        out.append('- 停止原因：正常完成或仍在运行中')
    out.append('')
    out.extend(['## 类型统计', ''])
    for msg_type, count in sorted(stats.get('by_type', {}).items()):
        out.append(f'- {msg_type}：{count}')
    if not stats.get('by_type'):
        out.append('- 暂无')
    out.append('')
    out.extend(['## 自查清单', ''])
    if not issues:
        out.append('未发现明显可疑项。')
    for category, items in issues.items():
        out.extend([f'### {category}', ''])
        for item in items[:80]:
            if item.get('duplicate'):
                out.append(f'- {item["label"]}')
                out.append(f'  - 第一条：{item["first_label"]}')
                out.append(f'  - 内容：{item["first_content"]}')
                out.append(f'  - 第二条：{item["second_label"]}')
                out.append(f'  - 内容：{item["second_content"]}')
                out.append(f'  - 相似度：{item["similarity"]}')
                out.append(f'  - 推荐保留：{item["preferred_label"]}')
                out.append(f'  - 处理：{"已自动去重" if item.get("auto_removed") else "仅提示，未自动删除"}')
                out.append(f'  - 原因：{item["detail"]}')
                sources = [s for s in (item.get('first_source'), item.get('second_source')) if s]
                if sources:
                    out.append(f'  - 截图：{", ".join(dict.fromkeys(sources))}')
                continue
            if item.get('time_anomaly'):
                out.append(f'- {item["label"]}')
                out.append(f'  - 当前识别时间：{item["current_time"]}')
                if item.get('raw_time'):
                    out.append(f'  - OCR 原始时间文字：{item["raw_time"]}')
                if item.get('previous_time'):
                    out.append(f'  - 前一个时间：{item["previous_time"]}')
                if item.get('next_time'):
                    out.append(f'  - 后一个时间：{item["next_time"]}')
                out.append(f'  - 原因：{item["detail"]}')
                if item.get('source'):
                    out.append(f'  - 截图：{item["source"]}')
                continue
            if item.get('pair'):
                out.append(f'- {item["label"]}')
                out.append(f'  - 上一条：{item["previous_label"]}')
                out.append(f'  - [{item["previous_sender"]}] {item["previous_content"]}')
                out.append(f'  - 下一条：{item["current_label"]}')
                out.append(f'  - [{item["current_sender"]}] {item["current_content"]}')
                out.append(f'  - 原因：{item["detail"]}')
                sources = [s for s in (item.get('previous_source'), item.get('current_source')) if s]
                if sources:
                    out.append(f'  - 建议人工核对：{", ".join(dict.fromkeys(sources))}')
                continue
            out.append(f'- {item["label"]}')
            out.append(f'  - 内容：{item["content"]}')
            out.append(f'  - 原因：{item["detail"]}')
            if item['source']:
                out.append(f'  - 截图：{item["source"]}')
        if len(items) > 80:
            out.append(f'- 还有 {len(items) - 80} 项未展开')
        out.append('')
    out.extend(['## 使用建议', ''])
    out.append('- 质量报告只提示疑似问题，不代表一定识别错误。')
    out.append('- 优先检查“疑似重复消息”“疑似消息截断”和“疑似发送者归属异常”。')
    out.append('- 如需核对原图，可在本次 work 目录的 screenshots 文件夹中按页码打开截图。')
    path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')

def split_records_by_month(records):
    groups = {}
    for record in records:
        t = parse_record_time(record)
        key = t.strftime('%Y-%m') if t else 'unknown'
        groups.setdefault(key, []).append(record)
    return groups

def record_bbox(record):
    bbox = record.get('bbox') or [0, 0, 0, 0]
    if len(bbox) != 4:
        return 0, 0, 0, 0
    return bbox

def horizontal_overlap_ratio(a_box, b_box):
    ax1, _ay1, ax2, _ay2 = a_box
    bx1, _by1, bx2, _by2 = b_box
    overlap = min(ax2, bx2) - max(ax1, bx1)
    if overlap <= 0:
        return 0.0
    return overlap / max(1, min(ax2 - ax1, bx2 - bx1))

def page_vertical_bounds(records):
    bounds = {}
    for record in records:
        page = int(record.get('page') or 0)
        if page <= 0:
            continue
        _x1, y1, _x2, y2 = record_bbox(record)
        if y2 <= y1:
            continue
        top, bottom = bounds.get(page, (y1, y2))
        bounds[page] = (min(top, y1), max(bottom, y2))
    return bounds

def filter_media_neighbor_text(records):
    media_types = {'mini_program', 'image', 'red_packet', 'transfer'}
    media_records = [r for r in records if r.get('type') in media_types]
    if not media_records:
        return records
    vertical_bounds = page_vertical_bounds(records)
    filtered = []
    for record in records:
        if record.get('type') != 'text':
            filtered.append(record)
            continue
        page = int(record.get('page') or 0)
        sender = record.get('sender')
        record_box = record_bbox(record)
        _x1, y1, _x2, y2 = record_box
        page_top, page_bottom = vertical_bounds.get(page, (0, 0))
        near_page_top = page_top and y2 <= page_top + 260
        near_page_bottom = page_bottom and y1 >= page_bottom - 260
        remove = False
        for media in media_records:
            if media.get('sender') != sender:
                continue
            media_page = int(media.get('page') or 0)
            media_box = record_bbox(media)
            mx1, my1, mx2, my2 = media_box
            same_page_overlap = (
                _x2 >= mx1 - 80
                and _x1 <= mx2 + 80
                and y2 >= my1 - 280
                and y1 <= my2 + 120
            )
            cross_page_overlap = horizontal_overlap_ratio(record_box, media_box) >= 0.55
            previous_page_residue = (
                media_page == page + 1
                and near_page_bottom
                and cross_page_overlap
                and 420 <= y1 - my1 <= 1350
            )
            next_page_residue = (
                media_page == page - 1
                and near_page_top
                and cross_page_overlap
                and 420 <= my1 - y1 <= 1350
            )
            if (page == media_page and same_page_overlap) or previous_page_residue or next_page_residue:
                remove = True
                break
        if not remove:
            filtered.append(record)
    return filtered

def format_txt_lines(records):
    lines = []
    for record in records:
        content = placeholder_content(record.get('type'), record.get('content', ''))
        prefix = f"{record.get('sender', '')}："
        if record.get('time'):
            prefix = f"{record.get('time')} {prefix}"
        lines.append(prefix + content)
    return lines

def write_standard_outputs(md, json_path, summary_path, split_dir, txt_path, records, profile, quality_path=None, raw_pages=None, stopped_reason=''):
    isolated_records = [record for record in records if record.get('type') in ('quoted_context', 'non_chat_time')]
    records = [record for record in records if record.get('type') not in ('quoted_context', 'non_chat_time')]
    records = filter_media_neighbor_text(records)
    records, duplicate_issues = fuzzy_dedupe_records(records)
    records = quarantine_unreliable_times(records)
    stats = compute_export_stats(records)
    export_markdown(md, records, profile, stats)
    if quality_path:
        export_quality_report(quality_path, records, profile, stats, raw_pages, stopped_reason, duplicate_issues, isolated_records)
    if txt_path:
        txt_path.write_text('\n'.join(format_txt_lines(records)) + ('\n' if records else ''), encoding='utf-8-sig')
    if json_path:
        write_json(json_path, records, profile, stats)
    if summary_path:
        summary_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    if split_dir:
        split_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]+', '_', profile.get('target_name') or '聊天记录')
        for month, month_records in split_records_by_month(records).items():
            export_markdown(split_dir / f'{safe_name}_{month}.md', month_records, profile, compute_export_stats(month_records))

def key(line):
    speaker, text = split_speaker_text(line)
    text = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', text.lower())
    return speaker + ':' + text

def content_key(line):
    _speaker, text = split_speaker_text(line)
    return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', text.lower())

def compact_message_body(line):
    _speaker, text = split_speaker_text(line)
    return re.sub(r'[\s，。？！?!.、：:]', '', text)

def same_speaker(a, b):
    return split_speaker_text(a)[0] == split_speaker_text(b)[0]

def looks_like_reply_preview(text):
    value = re.sub(r'\s+', ' ', text or '').strip()
    return bool(re.match(r'^[\u4e00-\u9fffA-Za-z0-9_]{1,12}\s*[:：]\s*[\u4e00-\u9fffA-Za-z0-9]', value))

def message_quality(line):
    _speaker, text = split_speaker_text(line)
    stripped = text.strip()
    if looks_like_tsv_dump(text):
        return -100
    if stripped in ('日', '加'):
        return -100
    if re.fullmatch(r'[0-9０-９]{1,3}', stripped):
        return 6
    zh = re.findall(r'[\u4e00-\u9fff]', text)
    latin = re.findall(r'[A-Za-z]', text)
    symbol_noise = re.findall(r'[|丨\\\\/_=~`^{}\\[\\]<>]', text)
    if len(zh) <= 1 and (len(latin) >= 1 or symbol_noise):
        return -100
    if len(zh) == 0 and len(text.strip()) <= 8:
        return -100
    score = len(zh) * 3 + len(re.findall(r'[A-Za-z0-9]', text))
    score -= len(symbol_noise) * 4
    if text.endswith('?') or text.endswith('？') or text.endswith('。') or text.endswith('!') or text.endswith('！'):
        score += 2
    return score

def is_valid_short_message(line):
    _speaker, text = split_speaker_text(line)
    stripped = text.strip()
    if re.fullmatch(r'[0-9０-９]{1,3}', stripped):
        return True
    if stripped in ('行', '坏', '好', '嗯', '啊', '哦', '噢', '不', '对', '来', '到', '是'):
        return True
    return False

def similarity(a, b):
    if not a or not b:
        return 0.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter and shorter in longer:
        return len(shorter) / max(1, len(longer))
    common = 0
    used = [False] * len(longer)
    for ch in shorter:
        for i, other in enumerate(longer):
            if not used[i] and ch == other:
                used[i] = True
                common += 1
                break
    return common * 2 / max(1, len(shorter) + len(longer))

def add_message(lines, seen, message, page, threshold):
    line_text = message['line'] if isinstance(message, dict) else str(message)
    if isinstance(message, dict) and message.get('kind') == 'time':
        return False
    msg_type = infer_message_type(message) if isinstance(message, dict) else 'text'
    if isinstance(message, dict) and '领取了你的红包' in message.get('text', ''):
        return False
    if isinstance(message, dict) and message.get('kind') == 'sticker':
        speaker = message.get('speaker', '')
        fp = message.get('fingerprint', '')
        if len(lines) >= 2 and lines[-1] == line_text and lines[-2] == line_text:
            return False
        recent_seen = [item for item in seen if 0 < page - item['page'] <= 2]
        for item in recent_seen:
            if item.get('kind') == 'sticker' and item.get('speaker') == speaker and item.get('fingerprint') == fp:
                item['page'] = page
                return False
        seen.append({'kind': 'sticker', 'speaker': speaker, 'fingerprint': fp, 'index': len(lines), 'page': page})
        del seen[:-220]
        lines.append(line_text)
        return True
    if isinstance(message, dict) and msg_type in ('mini_program', 'image', 'red_packet', 'transfer', 'card', 'call'):
        speaker = message.get('speaker', '')
        msg_top = int(message.get('top', 0) or 0)
        recent_seen = [item for item in seen if 0 <= page - item['page'] <= 1]
        for item in recent_seen:
            if item.get('kind') == msg_type and item.get('speaker') == speaker:
                old_top = int(item.get('top', 0) or 0)
                same_position = abs(msg_top - old_top) <= 80
                scrolled_overlap = page > item['page'] and msg_top < old_top - 80
                if same_position or scrolled_overlap:
                    item['page'] = page
                    item['top'] = msg_top
                    return False
        seen.append({'kind': msg_type, 'speaker': speaker, 'index': len(lines), 'page': page, 'top': msg_top})
        del seen[:-220]
        lines.append(line_text)
        return True
    if isinstance(message, dict) and (message.get('bubble_id') or message.get('kind') in ('recall', 'quoted_context', 'non_chat_time')):
        # Keep geometric evidence for the structured dedupe layer; do not drop
        # distinct bubbles with the older character-bag/substring heuristics.
        lines.append(line_text)
        return True
    k = key(line_text)
    ck = content_key(line_text)
    q = message_quality(line_text)
    if (len(ck) < 2 and not is_valid_short_message(line_text)) or q < 0:
        return False
    _speaker, body_text = split_speaker_text(line_text)
    if looks_like_reply_preview(body_text):
        return False
    if 3 <= len(ck) <= 12:
        for old_line in reversed(lines[-6:]):
            if same_speaker(line_text, old_line):
                continue
            old_ck = compact_message_body(old_line)
            if len(old_ck) >= len(ck) + 2 and ck in old_ck:
                return False
    if 2 <= len(ck) <= 6:
        for old_line in reversed(lines[-5:]):
            if same_speaker(line_text, old_line):
                old_ck = compact_message_body(old_line)
                if len(old_ck) >= len(ck) + 2 and ck in old_ck:
                    return False
    # Only dedupe against nearby pages. Real repeated phrases much later should be kept.
    recent_seen = [item for item in seen if page - item['page'] <= 2 and item.get('kind') != 'sticker' and 'content_key' in item]
    for item in recent_seen:
        sim = similarity(ck, item['content_key'])
        old_line = lines[item['index']]
        short_overlap = same_speaker(line_text, old_line) and max(len(ck), len(item['content_key'])) <= 8 and sim >= 0.70
        if k == item['key'] or sim >= threshold or short_overlap:
            # Refresh the last-seen page even when keeping the existing text;
            # otherwise repeated overlap can be re-added a few pages later.
            item['page'] = page
            if q > item['quality'] + 2 or len(ck) > len(item['content_key']) + 1:
                lines[item['index']] = line_text
                item['key'] = k
                item['content_key'] = ck
                item['quality'] = q
            return False
    seen.append({'key': k, 'content_key': ck, 'quality': q, 'index': len(lines), 'page': page})
    del seen[:-220]
    lines.append(line_text)
    return True

def write_progress_outputs(txt, raw, lines, raw_pages):
    if txt:
        txt.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8-sig')
    raw.write_text('\n\n'.join(f'===== PAGE {p:03d} =====\n{t}' for p, t in raw_pages), encoding='utf-8')

def selected_window_alive(hwnd):
    if not hwnd:
        return True
    try:
        return bool(user32.IsWindow(hwnd)) and bool(user32.IsWindowVisible(hwnd))
    except Exception:
        return True

def main():
    a = args()
    if a.calibrate:
        print('请先把微信聊天窗口放在屏幕上，不要遮挡。3 秒后截取全屏校准图。')
        for i in range(3, 0, -1):
            print(i)
            time.sleep(1)
        make_calibration_image()
        return

    tesseract_exe = find_tesseract(a.tesseract_path)
    if a.ocr_engine == 'custom':
        if not a.custom_ocr_tsv_command:
            raise RuntimeError('使用 --ocr-engine custom 时，必须提供 --custom-ocr-tsv-command。')
        tesseract_exe = '__custom_ocr__'
        print('OCR 引擎：Custom command')
    elif a.ocr_engine in ('auto', 'tesseract') and tesseract_exe:
        print('OCR 引擎：Tesseract -', tesseract_exe)
    elif a.ocr_engine == 'tesseract':
        raise RuntimeError('找不到 Tesseract。请安装 Tesseract OCR 和 chi_sim 中文语言包，或用 --tesseract-path 指定 tesseract.exe。')
    else:
        print('OCR 引擎：Windows OCR')

    capture_hwnd = None
    if a.region:
        box = choose_region(a.region)
        box = crop_bbox(box, a.crop)
        print('开始识别。固定截图区域：', box)
        print('提示：运行期间不要移动微信窗口；工具会把鼠标放到该区域中心滚动。')
    else:
        hwnd, title, box = choose_window(a.window_title)
        capture_hwnd = hwnd
        box = crop_bbox(box, a.crop)
        print('已选择窗口：', title)
        print('开始识别。窗口截图区域：', box)
        print('提示：运行期间不要移动微信窗口；工具会自动把鼠标放到窗口中部滚动。')

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    session = WORK / stamp
    shots = session / 'screenshots'
    shots.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    chat_profile = resolve_chat_profile(a, locals().get('title', ''))
    safe_target = re.sub(r'[\\/:*?"<>|]+', '_', chat_profile.get('target_name') or '聊天记录')
    txt = EXPORTS / f'{safe_target}_聊天记录_{stamp}.txt' if a.keep_txt else None
    md = EXPORTS / f'{safe_target}_聊天记录_完整_{stamp}.md'
    quality = EXPORTS / f'{safe_target}_质量检查_{stamp}.md'
    json_out = EXPORTS / f'{safe_target}_聊天记录_{stamp}.json' if a.output_json else None
    summary_out = EXPORTS / f'{safe_target}_聊天统计_{stamp}.json' if a.output_json else None
    split_dir = EXPORTS / f'{safe_target}_聊天记录_分片_{stamp}' if a.split_md else None
    raw = session / 'raw_ocr_pages.txt'
    write_progress_outputs(None, raw, [], [])
    write_standard_outputs(md, json_out, summary_out, split_dir, txt, [], chat_profile, quality, [])
    print('Markdown 会边识别边保存：', md)
    print('质量检查会边识别边保存：', quality)
    if txt:
        print('TXT 会边识别边保存：', txt)
    if json_out:
        print('JSON 会边识别边保存：', json_out)
    try:
        (session / 'selected_window.txt').write_text(str(locals().get('title', '')) + '\n' + str(box), encoding='utf-8')
    except Exception:
        pass
    if not a.region:
        print('请确认微信窗口没有被黑色运行窗口或文件夹遮挡。')
        print('需要的话，把这个黑色窗口拖到屏幕左侧/下方，不要盖住微信聊天内容。')
        input('确认没有遮挡后按回车开始截图...')

    focus_region_once(box, a.scroll_x_ratio, a.scroll_y_ratio)
    scroll_pulse, scroll_count = compute_scroll_plan(box, a)
    print(f'自动滚动：每次 {scroll_pulse} 格 x {scroll_count} 次，约 {a.scroll_ratio:.0%} 个窗口高度')

    lines, raw_pages, seen = [], [], []
    structured_records = []
    time_context = {'run_datetime': dt.datetime.now(), 'last_datetime': None}
    empty_pages = 0
    duplicate_screens = 0
    previous_screen = None
    stopped_reason = ''
    for page in range(1, a.max_pages + 1):
        if len(lines) >= a.max_lines:
            stopped_reason = f'已达到导出上限：{a.max_lines} 行'
            break
        if not selected_window_alive(capture_hwnd):
            stopped_reason = '微信窗口已关闭或不可见，已保存当前进度并停止。'
            break
        img = capture_window_image(capture_hwnd if a.print_window else None, box, False)
        png = shots / f'page_{page:03d}.png'
        img.save(png)
        if looks_like_console_capture(img):
            if page == 1:
                raise RuntimeError('截图看起来像黑色命令窗口，而不是微信。请重新运行，选择微信窗口后把黑色窗口移开再按回车。调试截图：' + str(png))
            stopped_reason = '截图不再像微信聊天窗口，已保存当前进度并停止。调试截图：' + str(png)
            break
        if previous_screen is not None:
            diff_score = screenshot_difference_score(previous_screen, img)
            if diff_score < 0.8:
                duplicate_screens += 1
                if duplicate_screens >= 2:
                    stopped_reason = '连续截图没有明显变化，可能已经到底或微信未响应滚动，已保存当前进度并停止。'
                    break
            else:
                duplicate_screens = 0
        previous_screen = img.copy()
        print(f'识别第 {page} 页...')
        text, page_lines = ocr_page(img, page, shots, a, tesseract_exe)
        raw_pages.append((page, text))
        before_count = len(lines)
        for line in page_lines:
            if isinstance(line, dict) and line.get('kind') == 'time':
                consume_time_line(line, time_context)
                continue
            if len(lines) >= a.max_lines:
                break
            if add_message(lines, seen, line, page, a.dedupe_threshold):
                structured_records.append(build_structured_message(line, chat_profile, time_context.get('current_datetime'), png, time_context))
        added_count = len(lines) - before_count
        if added_count <= 0:
            empty_pages += 1
        else:
            empty_pages = 0
        write_progress_outputs(None, raw, lines, raw_pages)
        write_standard_outputs(md, json_out, summary_out, split_dir, txt, structured_records, chat_profile, quality, raw_pages)
        print(f'第 {page} 页 OCR 原始字符数：{len(text)}，消息数：{len(page_lines)}，新增：{added_count}，已导出：{len(lines)}/{a.max_lines}')
        if len(lines) >= a.max_lines:
            stopped_reason = f'已达到导出上限：{a.max_lines} 行'
            break
        if empty_pages >= a.max_empty_pages:
            stopped_reason = f'连续 {empty_pages} 页没有新增消息，已保存当前进度并停止。'
            break
        scroll_region(box, scroll_pulse, scroll_count, a.scroll_pulse_delay, a.scroll_x_ratio, a.scroll_y_ratio)
        time.sleep(a.delay)

    write_progress_outputs(None, raw, lines, raw_pages)
    write_standard_outputs(md, json_out, summary_out, split_dir, txt, structured_records, chat_profile, quality, raw_pages, stopped_reason)
    if stopped_reason:
        print(stopped_reason)

    debug = session / 'debug_first_page.png'
    first = shots / 'page_001.png'
    if first.exists():
        shutil.copy2(first, debug)
    if not a.keep_screenshots:
        # Keep a bounded set of raw page screenshots and all per-page message text.
        # OCR threshold variants are large, so only keep those for the first page.
        retain_pages = max(1, int(getattr(a, 'debug_retain_pages', 300) or 1))
        for item in shots.iterdir():
            m = re.fullmatch(r'page_(\d{3})\.png', item.name)
            if m and int(m.group(1)) <= retain_pages:
                continue
            if item.name.endswith('_messages.txt'):
                continue
            if item.name.startswith('page_001'):
                continue
            item.unlink(missing_ok=True)

    if not lines:
        print('警告：没有识别到任何文本。')
        if not tesseract_exe:
            print('建议安装 Tesseract OCR，并确认安装 chi_sim 中文语言包。')
    print('完成。')
    print('Markdown：', md)
    print('质量检查：', quality)
    if txt:
        print('TXT：', txt)
    if json_out:
        print('JSON：', json_out)
        print('统计：', summary_out)
    if split_dir:
        print('分片目录：', split_dir)
    print('原始 OCR：', raw)
    print('调试截图：', debug)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('运行失败：')
        print(e)
        sys.exit(1)
