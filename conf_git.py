"""
电影自动解说工作流 — 全局配置文件（模板）
将本文件复制为 conf.py 并填入你的配置
"""
import os
import shutil

# ===== 路径配置 =====
# 项目根目录（自动检测）
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.join(PROJECT_ROOT, "workspace")
BGM_LIBRARY_DIR = os.path.join(PROJECT_ROOT, "bgm_library")
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")

# ffmpeg 可执行文件路径（Windows 使用 imageio-ffmpeg）
import imageio_ffmpeg
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
_ffprobe_near_ffmpeg = os.path.join(
    os.path.dirname(FFMPEG_BIN),
    os.path.basename(FFMPEG_BIN).replace("ffmpeg", "ffprobe", 1),
)
FFPROBE_BIN = _ffprobe_near_ffmpeg if os.path.exists(_ffprobe_near_ffmpeg) else (shutil.which("ffprobe") or "ffprobe")

# ===== Whisper 配置 (M1 字幕提取) =====
# 用户需要填写：本地模型路径，留空则自动下载
WHISPER_MODEL_PATH = ""
WHISPER_MODEL_SIZE = "small"  # 可选: tiny/base/small/medium/large-v3
WHISPER_DEVICE = "cpu"  # GPU 使用 "cuda"
WHISPER_COMPUTE_TYPE = "float32"  # CPU 使用 float32
WHISPER_VAD_FILTER = True  # 启用 VAD 过滤

# ===== 场景检测配置 (M2) =====
SCENE_THRESHOLD = 27.0  # ContentDetector 阈值，越低越敏感
SCENE_MIN_DURATION_SEC = 2.0  # 最短场景时长（秒）
SCENE_THUMBNAIL_WIDTH = 320  # 缩略图宽度

# ===== 信息采集配置 (M3) =====
# 用户需要填写 TMDB_API_KEY（可选）
TMDB_API_KEY = ""
TMDB_BASE_URL = "https://api.themoviedb.org/3"
OMDB_API_KEY = "thewdb"  # 公共测试 key
OMDB_BASE_URL = "https://www.omdbapi.com/"
DOUBAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
INFO_SEARCH_SOURCES = ["douban", "baidu_baike", "tmdb", "imdb_omdb"]  # 采集源优先级
M3_QWEN_MODEL = "qwen3.5-plus"  # M3 剧情增强模型
M3_SYNOPSIS_TARGET_CHARS = 1000  # 目标剧情梳理字数
M3_KEYSCENE_MIN = 10  # 关键场景最小数量
M3_KEYSCENE_MAX = 20  # 关键场景最大数量

# ===== 时间戳匹配配置 (M3 关键场景模式) =====
TIMESTAMP_MIN_INTERVAL = 30  # 场景间最小间隔（秒）
TIMESTAMP_MIN_DURATION = 8  # 单场景最小时长（秒）
SAMPLE_DIALOGUE_COUNT = 6  # 每场景生成台词数量

# ===== LLM API 配置 (M4 剧本生成) =====
# 用户必须填写 DASHSCOPE_API_KEY
DASHSCOPE_API_KEY = ""
QWEN_MODEL = "qwen-max"
QWEN_MAX_TOKENS = 8192
QWEN_TEMPERATURE = 0.7
SCRIPT_TARGET_DURATION_MIN = 20  # 目标解说时长（分钟）
SCRIPT_TARGET_WORDS = 7000  # 目标解说词字数
WORDS_PER_MINUTE = 350  # 中文语速
EMOTION_TYPES = ["平静", "紧张", "悲伤", "欢快", "震撼", "温馨", "悬疑"]

# ===== TTS 配置 (M5 语音合成) =====
# 用户需要填写模型路径
COSYVOICE_MODEL_PATH = ""
TTS_DEVICE = "cpu"
TTS_SAMPLE_RATE = 22050
TTS_DEFAULT_SPEED = 1.0
DEFAULT_VOICE_REF = ""  # 运行时通过 CLI 传入参考音频路径

# ===== BGM 配置 (M6) =====
BGM_VOLUME_DB = -15  # BGM 音量（相对人声，dB）
BGM_FADE_DURATION_SEC = 2.0  # 淡入淡出时长（秒）
BGM_SKIP_IF_EMPTY = True  # 曲库为空时自动跳过

# ===== 视频配置 (M7 裁切 / M8 合成) =====
OUTPUT_RESOLUTION = "1280:720"  # ffmpeg scale 格式
OUTPUT_FPS = 24
OUTPUT_VIDEO_CODEC = "libx264"
OUTPUT_AUDIO_CODEC = "aac"
OUTPUT_AUDIO_BITRATE = "192k"
OUTPUT_PRESET = "medium"
OUTPUT_CRF = 23
ORIGINAL_AUDIO_VOLUME = 0.1  # 原声保留比例
TRANSITION_DURATION_SEC = 0.5  # 片段间过渡时长

# ===== 日志配置 =====
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"