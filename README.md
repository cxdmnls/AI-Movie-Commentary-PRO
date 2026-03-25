# Movie Narrator

电影自动解说工作流 - 使用 AI 技术将电影自动转化为精彩的解说短视频。

## 功能特性

| 模块 | 功能 | 说明 |
|------|------|------|
| M1 | 字幕提取 | 使用 Faster-Whisper 本地语音识别 |
| M2 | 场景检测 | 使用 PySceneDetect 智能识别场景切换 |
| M3 | 信息采集 | 联网采集电影信息 + Qwen 结构化增强 |
| M4 | 剧本生成 | 基于 Qwen API 生成专业解说剧本 |
| M5 | TTS 合成 | 使用 CosyVoice 2 生成语音解说 |
| M6 | BGM 匹配 | 智能匹配合适的背景音乐 |
| M7 | 视频编辑 | 自动剪辑视频片段 |
| M8 | 最终合成 | 合成最终解说视频 |

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/cxdmnls/AI-Movie-Commentary-PRO.git
cd AI-Movie-Commentary-PRO
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 安装 CosyVoice 2（用于 TTS）

```bash
git clone https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice
pip install -e .
cd ..
```

### 4. 配置项目

将 `conf_git.py` 复制为 `conf.py` 并填写必要配置：

```bash
cp conf_git.py conf.py
```

#### 必须填写的配置项：

| 配置项 | 说明 | 获取方式 |
|--------|------|----------|
| `DASHSCOPE_API_KEY` | 阿里云 Qwen API 密钥 | [阿里云 DashScope](https://dashscope.console.aliyun.com/) |
| `WHISPER_MODEL_PATH` | Faster-Whisper 模型路径（可选） | 留空则自动下载 |
| `COSYVOICE_MODEL_PATH` | CosyVoice 模型路径 | 本地模型目录 |

#### 可选配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `TMDB_API_KEY` | 空 | TMDb API（电影信息增强） |
| `DEFAULT_VOICE_REF` | 空 | TTS 参考音频路径 |

### 5. 运行

#### 完整流程

```bash
python main.py run "你的电影.mp4" --name "电影名称" --voice "参考音色.wav"
```

#### 关键场景模式（推荐）

```bash
python main.py run_keyscene "你的电影.mp4" \
  --name "西虹市首富" \
  --srt "字幕文件.srt"
```

可选参数：
- `--keywords "关键词1,关键词2"` - 联网检索增强
- `--keywords-file keywords.txt` - 从文件读取关键词
- `--stop-at 4` - 运行到指定步骤后停止

#### 步骤说明

| 步骤 | 说明 |
|------|------|
| --stop-at 2 | 字幕提取 + 场景检测 |
| --stop-at 3 | M3 信息采集（生成 movie_info.json）|
| --stop-at 4 | M4 剧本生成（默认）|
| --stop-at 5 | M5-M7 素材生产 |
| --stop-at 8 | 完整流程 |

## 项目结构

```
Movie/
├── main.py                 # CLI 主入口
├── gradio_app.py          # Web 界面
├── conf.py                # 配置文件（本地）
├── conf_git.py            # 配置模板（上传）
├── requirements.txt       # Python 依赖
├── modules/               # 核心模块
│   ├── subtitle_extractor/  # M1 字幕提取
│   ├── scene_detector/      # M2 场景检测
│   ├── info_collector/      # M3 信息采集
│   ├── script_generator/    # M4 剧本生成
│   ├── tts_synthesizer/     # M5 TTS 合成
│   ├── bgm_matcher/         # M6 BGM 匹配
│   ├── video_editor/        # M7 视频编辑
│   └── final_composer/      # M8 最终合成
├── prompts/               # Prompt 模板
├── workspace/             # 工作目录（自动生成）
└── bgm_library/           # BGM 曲库
```

## 使用示例

### 准备你的工作目录

```
workspace/
└── 你的电影名/
    └── 你的电影名.srt    # 可选，提供 SRT 字幕文件
```

### 运行关键场景模式

```bash
# 基本用法
python main.py run_keyscene "电影.mp4" --name "电影名"

# 带字幕文件
python main.py run_keyscene "电影.mp4" --name "电影名" --srt "电影.srt"

# 带关键词检索
python main.py run_keyscene "电影.mp4" --name "电影名" \
  --srt "电影.srt" \
  --keywords "关键词1,关键词2,关键词3"
```

## 技术细节

### M3 信息采集特点

- 关键场景数量由 Qwen 自动评估（代码约束 10-20）
- 时间戳由 Qwen 输出 `subtitle_range`（字幕索引区间）后映射得到
- 代码强制顺序单调且不重叠
- 字幕来源优先级：`--srt` > `workspace/<电影名>.srt` > M1 自动提取
- 为避免“编造”内容，M3 会将联网检索结果作为上下文提供给 Qwen

## 许可证

MIT License