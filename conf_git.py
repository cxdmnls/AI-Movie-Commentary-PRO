"""
电影自动解说工作流 — 全局配置文件（模板）
将本文件复制为 conf.py 并填入你的配置
"""
import os
import shutil

# ===== 路径配置 =====
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.join(PROJECT_ROOT, "workspace")
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")

# ffmpeg 可执行文件路径（Windows 使用 imageio-ffmpeg）
import imageio_ffmpeg
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
_ffprobe_near_ffmpeg = os.path.join(
    os.path.dirname(FFMPEG_BIN),
    os.path.basename(FFMPEG_BIN).replace("ffmpeg", "ffprobe", 1),
)
FFPROBE_BIN = _ffprobe_near_ffmpeg if os.path.exists(_ffprobe_near_ffmpeg) else (shutil.which("ffprobe") or "ffprobe")

# ===== M1 字幕提取配置 =====
WHISPER_MODEL_PATH = ""  # 本地模型路径，留空则自动下载
WHISPER_MODEL_SIZE = "small"  # 可选: tiny/base/small/medium/large-v3
WHISPER_DEVICE = "cpu"  # GPU 使用 "cuda"
WHISPER_COMPUTE_TYPE = "float32"  # CPU 使用 float32
WHISPER_VAD_FILTER = True  # 启用 VAD 过滤

# ===== M3 信息采集配置 =====
TMDB_API_KEY = ""  # 可选，从 https://www.themoviedb.org 获取
TMDB_BASE_URL = "https://api.themoviedb.org/3"
OMDB_API_KEY = "thewdb"  # 公共测试 key
OMDB_BASE_URL = "https://www.omdbapi.com/"
DOUBAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
INFO_SEARCH_SOURCES = ["douban", "baidu_baike", "tmdb", "imdb_omdb"]
M3_LLM_API_KEY = ""  # 智增增API Key (从 https://zhizengzeng.com 获取)
M3_LLM_BASE_URL = "https://api.zhizengzeng.com/v1/chat/completions"
M3_LLM_MODEL = "gpt-4o-mini"  # 可选: gpt-4o-mini, gpt-5-mini, gpt-4o, deepseek-chat 等
M3_KEYSCENE_MIN = 10  # 关键场景最小数量
M3_KEYSCENE_MAX = 20  # 关键场景最大数量
SAMPLE_DIALOGUE_COUNT = 6  # 每场景生成台词数量
TIMESTAMP_MIN_INTERVAL = 30  # 场景间最小间隔（秒）
TIMESTAMP_MIN_DURATION = 8  # 单场景最小时长（秒）

# ===== M5 TTS 配置 =====
COSYVOICE_MODEL_PATH = ""  # CosyVoice2 模型路径
TTS_DEVICE = "cpu"
TTS_SAMPLE_RATE = 22050
TTS_DEFAULT_SPEED = 1.0
DEFAULT_VOICE_REF = ""  # 参考音频路径（运行时传入）
WORDS_PER_MINUTE = 350  # 中文语速

# ===== 日志配置 =====
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"