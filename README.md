# Movie Narrator

电影自动解说工作流 - 使用 AI 技术将电影自动转化为精彩的解说短视频。

## 功能特性

| 模块 | 功能 | 说明 |
|------|------|------|
| M1 | 字幕提取 | 使用 Faster-Whisper 本地语音识别 |
| M3 | 信息采集 | 联网采集电影信息 + LLM 结构化增强（支持 GPT/Gemini/Claude/DeepSeek 等） |
| M5 | TTS 合成 | 使用 CosyVoice 2 生成语音解说 |

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
| `M3_LLM_API_KEY` | 智增增 API 密钥（M3信息采集） | [智增增](https://zhizengzeng.com/) |
| `WHISPER_MODEL_PATH` | Faster-Whisper 模型路径（可选） | 留空则自动下载 |
| `COSYVOICE_MODEL_PATH` | CosyVoice 模型路径 | 本地模型目录 |

#### 可选配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `M3_LLM_MODEL` | gpt-4o-mini | M3 使用的模型（可用: gpt-4o-mini, gpt-5-mini, gpt-4o, deepseek-chat 等） |
| `TMDB_API_KEY` | 空 | TMDb API（电影信息增强） |

### 准备电影文件

将你的电影文件放在根目录下

### 5. 测试各模块

#### M1 字幕提取

```bash
python -m modules.subtitle_extractor.extractor <视频文件路径> <输出目录>
```

#### M3 信息采集

```bash
python -m modules.info_collector.run_keyscene_step3_zhizengzeng --workspace <workspace目录>
```

#### M5 TTS 合成

```bash
python -m modules.tts_synthesizer.synthesizer <文本> <参考音频路径> <输出文件路径>
```

## 项目结构

```
AI-Movie-Commentary-PRO/
├── conf_git.py            # 配置模板
├── requirements.txt       # Python 依赖
├── README.md             # 项目说明
├── LICENSE               # MIT 许可证
├── prompts/              # Prompt 模板
│   └── m3_keyscene.txt   # M3 提示词
└── modules/              # 核心模块
    ├── __init__.py
    ├── subtitle_extractor/   # M1 - 字幕提取
    ├── info_collector/       # M3 - 信息采集
    └── tts_synthesizer/      # M5 - TTS 合成
```

## 许可证

MIT License