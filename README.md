# Movie Narrator

电影自动解说工作流 - 使用 AI 技术自动生成电影解说短视频

## 项目简介

Movie Narrator 是一个自动化电影解说视频生成工具，通过 AI 技术将电影自动转化为精彩的解说短视频。该项目采用模块化设计，支持完整的自动化处理流程，从视频输入到最终解说视频输出。

## 功能特性

- **M1 字幕提取**: 使用 Faster-Whisper 进行本地语音识别，自动提取电影字幕
- **M2 场景检测**: 使用 PySceneDetect 智能识别电影场景切换点
- **M3 信息采集**: 自动从互联网采集电影相关信息（导演、演员、剧情等）
- **M4 剧本生成**: 基于阿里云 Qwen API 生成专业解说剧本
- **M5 TTS 合成**: 使用 CosyVoice 2 生成自然流畅的语音解说
- **M6 BGM 匹配**: 智能匹配合适的背景音乐
- **M7 视频编辑**: 自动剪辑视频片段
- **M8 最终合成**: 合成最终解说视频

## 技术栈

- **Python 3.10+**
- **CLI 框架**: Typer
- **语音识别**: Faster-Whisper
- **场景检测**: PySceneDetect + OpenCV
- **LLM**: 阿里云 DashScope (Qwen)
- **TTS**: CosyVoice 2
- **Web UI**: Gradio
- **数据处理**: Pydantic, Rich

## 项目结构

```
Movie/
├── main.py                  # CLI 主入口
├── gradio_app.py            # Gradio Web 界面
├── conf.py                  # 配置文件
├── requirements.txt         # Python 依赖
├── modules/                 # 核心模块
│   ├── subtitle_extractor.py    # M1 字幕提取
│   ├── scene_detector.py       # M2 场景检测
│   ├── info_collector.py       # M3 信息采集
│   ├── script_generator.py     # M4 剧本生成
│   ├── tts_synthesizer.py      # M5 TTS 合成
│   ├── bgm_matcher.py          # M6 BGM 匹配
│   ├── video_editor.py         # M7 视频编辑
│   └── final_composer.py       # M8 最终合成
├── prompts/                 # 提示词模板
├── bgm_library/             # BGM 音乐库
├── review/                  # 用户审核模块
├── tests/                   # 单元测试
└── workspace/               # 工作目录
```

## 环境配置

1. 克隆项目后安装依赖:

```bash
pip install -r requirements.txt
```

2. 安装 CosyVoice 2 (不在 PyPI，需从源码安装):

```bash
git clone https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice
pip install -e .
```

3. 配置环境变量:

在 `conf.py` 中配置:
- 阿里云 DashScope API Key
- 工作目录路径
- 其他参数

## 使用方法

### CLI 方式

```bash
python main.py run "电影名称" --video-path /path/to/movie.mp4
```

### Web 界面方式

```bash
python gradio_app.py
```

然后在浏览器中打开显示的地址。

## 注意事项

- 请确保你有使用电影内容的合法权限
- 大文件（如视频文件）已添加到 .gitignore，不会提交到仓库
- 首次运行需要下载模型，请耐心等待

## 许可证

MIT License
