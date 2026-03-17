# 电影自动解说工作流 — 架构设计 v2

> 更新：2026-03-16 | 状态：已实现（持续迭代）

## 1. 文档定位

本文件不再是"待搭建方案"，而是**对当前仓库真实实现的架构快照**。

- 主流程入口：`main.py`（Typer CLI）
- 可视化入口：`gradio_app.py`（Gradio 分步执行）
- 全局配置：`conf.py`
- 模块划分：`modules/`（M1-M8）+ `review/`
- 电影级自动化脚本示例：`workspace/西虹市首富/build_all.py`

### 1.1 工作流模式

系统支持**两种工作流模式**：

**模式 A：标准模式（`run` 命令）**
- 基于画面场景检测（M2）生成时间戳
- 适合"旁白解说"型视频

**模式 B：关键场景模式（`run_keyscene` 命令）**
- 基于 LLM 多轮质量校验的剧情关键点生成（整合自 `regenerate_movie_info_with_qwen.py`）
- 通过 unique_keywords 模糊匹配字幕计算时间戳（整合自 `add_keyscene_timestamps.py`）
- 适合"精选片段"型解说视频

## 2. 当前能力总览

- M1 字幕提取：faster-whisper，本地转录；不可用时自动降级占位字幕。
- M2 场景检测：PySceneDetect + 缩略图导出；不可用时按固定时长切分。
- M3 信息采集：豆瓣/百科/TMDb/IMDb-OMDb 聚合，可选 Qwen 结构化增强。
  - **标准模式**：简化增强，生成基础 key_scenes
  - **关键场景模式**：完整剧情结构，生成 20 个关键场景，包含 unique_keywords
- M4 剧本生成：Qwen 三轮生成（场景筛选/解说生成/情感标注）；失败时可降级生成最小可执行脚本。
  - **标准模式**：M4 执行完整三轮生成
  - **关键场景模式**：Step 3.5 导出 Markdown 剧本，Step 4 转换为标准 script.json
- M5 TTS：CosyVoice2 推理；模型不可用时使用 ffmpeg 生成占位音频。
- M6 BGM：按情感标签匹配曲库，空曲库可按配置自动跳过。
- M7 视频裁切：ffmpeg 快速裁切失败后自动回退精确重编码。
- M8 最终合成：TTS+BGM 混音后与视频片段合并，最终 concat 输出。
- 审核流程：CLI 交互审核（reviewer）+ Gradio JSON 可编辑审核。
  - **标准模式**：审核点1（M4后）+ 审核点2（M5-M7后）
  - **关键场景模式**：审核点1（M4后）+ 审核点2（M5-M7后）
- **关键场景生成**：M3 支持生成完整的剧情关键场景（20个），包含剧情结构、角色分析、时间戳匹配。
- **时间戳匹配**：基于 unique_keywords 模糊匹配字幕，自动计算 video_clip 时间戳。
- **剧本导出**：支持导出 Markdown 格式的人类可读剧本（Step 3.5）。

## 3. 主流程（代码实际编排）

```text
Step 1  初始化 workspace
Step 2  并行执行 M1 + M2 + M3
Step 3  M4 生成 script.json，并进入审核点 1
Step 4  并行执行 M5 + M6 + M7
Step 5  审核点 2（素材取舍）
Step 6  M8 合成 final.mp4
```

### 3.1 CLI 命令总览

#### `run` - 标准模式（完整链路）

```bash
python main.py run <video> --name <name> --voice <voice>
```

**参数：**
- `video`: 电影文件路径（mp4/mkv）
- `--name, -n`: 电影名称（用于信息采集和工作目录命名）
- `--voice, -v`: 参考音频路径（用于 TTS voice cloning）

**流程：** 执行 Step 1-6 完整流程，包含两个审核点。

#### `run_keyscene` - 关键场景模式

```bash
# 默认：运行到 Step 4（生成剧本，不需要 voice）
python main.py run_keyscene <video> --name <name>

# 运行到 Step 3（只生成 movie_info）
python main.py run_keyscene <video> --name <name> --stop-at 3

# 完整流程（需要 voice）
python main.py run_keyscene <video> --name <name> --voice <voice> --stop-at 8

# 跳过时间戳匹配
python main.py run_keyscene <video> --name <name> --skip-timestamp-match
```

**参数：**
- `video`: 电影文件路径
- `--name, -n`: 电影名称（**必需**）
- `--voice, -v`: 参考音频路径（**step >= 5 必需**）
- `--stop-at, -s`: 停止步骤（默认: 4）
  - `1` = 初始化（创建 workspace）
  - `2` = 预处理（字幕+场景检测）
  - `3` = M3 信息采集（生成 20 个关键场景）
  - `4` = 导出可读剧本 + 转换格式（默认，生成 Markdown + JSON）
  - `5` = M5-M7 素材生产（需要 `--voice`）
  - `6` = 素材审核
  - `7` = M8 最终合成（需要 `--voice`）
  - `8` = 同 step 7
- `--skip-timestamp-match`: 跳过字幕时间戳匹配（用于无字幕场景）

**关键场景模式流程：**
```text
Step 1  初始化 workspace
Step 2  M1 字幕提取 + M2 场景检测（并行）
Step 3  M3 关键场景生成（20个）+ 时间戳匹配
Step 3.5 导出可读剧本（Markdown）
Step 4  转换关键场景为标准 script.json
Step 5  并行生产（M5 TTS + M6 BGM + M7 视频裁切）
Step 6  素材审核
Step 7  M8 最终合成
```

**输出文件：**
- `movie_info.json` - 包含 20 个 key_scenes，每个场景有完整元数据
- `movie_script_user.md` - 人类可读的 Markdown 剧本
- `script.json` - 标准格式的剧本（供后续 M5-M8 使用）

```bash
python main.py resume <workspace_path> --step <step>
```

**参数：**
- `workspace_path`: 已有的 workspace 目录路径
- `--step, -s`: 从第几步开始（2-7，默认 2）
  - `2` = 预处理（M1+M2+M3）
  - `3` = 剧本生成 + 审核（M4 + 审核点1）
  - `4` = 素材生产（M5-M7 并行，需要 `--voice`）
  - `5` = 素材审核（审核点2）
  - `6` = 最终合成（M8）
  - `7` = 同 step 6
- `--video`: 电影文件路径（step 2/4 需要）
- `--voice, -v`: 参考音频路径（step 4+ 需要）

#### `export-script` - 导出可读剧本

```bash
# 从已有 workspace 导出
python main.py export-script <workspace_path>

# 指定输出路径
python main.py export-script <workspace_path> --output <output_path>
```

**参数：**
- `workspace_path`: workspace 目录路径
- `--output, -o`: 输出文件路径（默认: workspace/movie_script_user.md）

**用途：** 当手动修改了 movie_info.json 后，重新导出 Markdown 剧本。

### 3.2 执行模式对比

| 维度 | 标准模式 (`run`) | 关键场景模式 (`run_keyscene`) |
|------|------------------|------------------------------|
| **场景来源** | M2 画面场景检测 | M3 LLM 剧情理解生成 |
| **场景数量** | 画面切换决定（几十个） | 固定 20 个（可配置） |
| **时间戳** | 画面切换点 | 关键词模糊匹配字幕 |
| **解说词** | M4 自动生成 narration | 复用 summary（需人工改写） |
| **适用场景** | 旁白解说型 | 精选片段型 |
| **审核点** | 2个（Step 3 后剧本审核 + Step 5 后素材审核） | 1个（Step 6 素材审核，Step 3.5 导出的 Markdown 剧本可人工预览） |

### 3.3 并行执行机制

- 标准模式 Step 2：`M1 + M2 + M3` 并行
- 标准模式 Step 4：`M5 + M6 + M7` 并行
- 关键场景模式 Step 2：`M1 + M2` 并行（M3 单独在 Step 3 执行）

并行阶段由 `ThreadPoolExecutor(max_workers=3)` 驱动。

### 3.4 审核点

- 审核点 1：`review.Reviewer.review_script()`
  - 可确认、编辑 segment、删除 segment、要求重新生成。
- 审核点 2：`review.Reviewer.review_materials()`
  - 可确认全部、标记跳过 segment、移除某段 BGM。

## 4. 模块实现说明（按 M1-M8）

### M1 字幕提取（`modules/subtitle_extractor/extractor.py`）

- 输入：视频文件路径。
- 输出：`subtitles.json`（`[{start, end, text}]`）。
- 关键实现：ffmpeg 抽音频 -> faster-whisper 转录。
- 降级策略：模型不可用或转录异常时，按时长分片生成占位字幕。

### M2 场景检测（`modules/scene_detector/detector.py`）

- 输入：视频文件路径。
- 输出：`scenes.json` + `scenes_thumbnails/*.jpg`。
- 关键实现：ContentDetector 检测场景边界，提取每段中点缩略图。
- 降级策略：PySceneDetect 不可用时按固定时长切分。

### M3 信息采集（`modules/info_collector/collector.py`）

**两种工作模式：**

**模式 A：标准模式（`collect()`）**
- 多源采集：`douban`、`baidu_baike`、`tmdb`、`imdb_omdb`（由 `INFO_SEARCH_SOURCES` 控制）。
- 可选增强：调用 Qwen 简化增强（生成基础 `key_scenes`）。
- 目标字段：`title/year/genre/synopsis/characters/plot_structure/key_scenes/emotional_arc`。

**模式 B：关键场景模式（`collect(keyscene_mode=True)`）**
- 使用 `_enrich_with_llm_v2()` 方法（整合自 `regenerate_movie_info_with_qwen.py`）生成完整剧情结构
- 生成 20 个关键场景（`M3_KEYSCENE_COUNT` 可配置）
- 每个场景包含：
  - 基础信息：`scene_id`, `phase`, `summary`, `importance`, `suggested_emotion`
  - 剧情要素：`scene_goal`, `conflict`, `turning_point`, `location`
  - 执行信息：`characters_present`, `visual_tone`, `dialogue_focus`, `action_line`
  - 关键词：`unique_keywords`（用于字幕匹配）
  - 对话：`sample_dialogue`（2句示例对白）
  - 元数据：`score_reason`, `confidence`
- 质量保障：
  - 自动质量校验（`_assess_quality_v2`）
  - 多轮自动修正（max 3 rounds）
  - 递归清理来源字段

**时间戳匹配（`_match_timestamps()`）**
- 功能：基于 `unique_keywords` 模糊匹配字幕，计算 `video_clip` 时间戳（整合自 `add_keyscene_timestamps.py`）
- 依赖：`rapidfuzz` 库（优先）或 `difflib`（降级）
- 算法：多关键词匹配 → 计算时间中心 → 前后扩展窗口 → 确保最小间隔

### M4 剧本生成（`modules/script_generator/generator.py`）

**标准模式（`generate()`）**
- 三轮流程：
  - scene selection（场景筛选）
  - narration generation（解说生成）
  - emotion tagging（情感标注）
- Prompt 文件：`prompts/*.txt`（位于项目根目录）。
- 输出：`script.json`（含 `segments`）。
- 降级策略：LLM 调用失败时，生成最小可执行脚本（确保链路可继续）。

**关键场景模式集成**
- 导出可读剧本：`export_readable_script()` → `ScriptExporter`
- 输出：`movie_script_user.md`（Markdown 格式）
- 内容结构：
  - 头部：标题、年份、类型、简介、主要人物
  - 章节：6 个 phase（setup → resolution）
  - 场景：20 个 key_scenes 的详细信息
  - 使用建议

**剧本导出器（`modules/script_generator/exporters.py`）**
- `ScriptExporter.export_markdown()`：导出 Markdown 格式
- `ScriptExporter.export_json()`：导出格式化的 JSON（调试用）

### M5 TTS 合成（`modules/tts_synthesizer/synthesizer.py`）

- 目标：按 segment 文本生成语音，并写回 `tts_audio_path`。
- 关键逻辑：
  - 根据片段时长估算语速并自适应。
  - 校验 TTS 时长偏差（默认容差 20%）。
- 降级策略：CosyVoice 不可用时生成可执行占位音频。

### M6 BGM 匹配（`modules/bgm_matcher/matcher.py`）

- 基于 `emotion` 从 `bgm_library/<情感>/` 随机选曲。
- 处理流程：裁切 -> 淡入淡出 -> 音量调整。
- 当曲库为空且 `BGM_SKIP_IF_EMPTY=True` 时直接跳过。

### M7 视频裁切（`modules/video_editor/editor.py`）

- 优先 `-c copy` 快速裁切，失败后自动回退精确重编码。
- 输出字段：`video_clip_path`、`video_clip_duration`。
- 支持根据配置保留原声或降音/静音。

### M8 最终合成（`modules/final_composer/composer.py`）

- 单段：视频 + TTS + 可选 BGM 混流。
- 全片：合成所有未 `skip` 片段并 concat。
- 输出：`workspace/{movie}/output/final.mp4`。

## 5. 实际目录结构（当前仓库）

```text
Movie/
├── main.py                          # CLI 主入口（支持 run/run_keyscene/resume/export-script）
├── gradio_app.py                    # Gradio Web 界面
├── conf.py                          # 全局配置（含关键场景模式配置）
├── requirements.txt                 # 依赖（新增 rapidfuzz）
├── prompts/                         # Prompt 模板（位于项目根目录）
│   ├── plot_enhancement.txt
│   ├── narration_gen.txt
│   ├── emotion_tagging.txt
│   └── whisper_initial.txt
├── modules/
│   ├── subtitle_extractor/          # M1: 字幕提取
│   ├── scene_detector/              # M2: 场景检测
│   ├── info_collector/              # M3: 信息采集
│   │   ├── collector.py             #   主采集器（支持 keyscene_mode，整合 LLM 增强+时间戳匹配）
│   │   └── utils.py                 #   工具函数
│   ├── script_generator/            # M4: 剧本生成
│   │   ├── generator.py             #   主生成器
│   │   └── exporters.py             #   剧本导出器
│   ├── tts_synthesizer/             # M5: TTS 合成
│   ├── bgm_matcher/                 # M6: BGM 匹配
│   ├── video_editor/                # M7: 视频裁切
│   └── final_composer/              # M8: 最终合成
├── review/                          # 用户审核模块
├── tests/                           # 单元测试
├── bgm_library/                     # BGM 曲库
│   ├── 震撼/
│   ├── 悬疑/
│   ├── 悲伤/
│   └── 紧张/
└── workspace/
    └── {movie_name}/
        ├── subtitles.json           # M1 输出：字幕列表
        ├── scenes.json              # M2 输出：画面场景
        ├── movie_info.json          # M3 输出：电影信息（含 20 个 key_scenes）
        ├── movie_script_user.md     # [新增] M3.5 输出：可读剧本
        ├── script.json              # M4 输出：标准剧本
        ├── segments_reviewed.json   # M5 审核后输出
        ├── scenes_thumbnails/       # M2 输出：场景缩略图
        ├── tts_audio/               # M5 输出：TTS 音频
        ├── bgm_clips/               # M6 输出：BGM 片段
        ├── video_clips/             # M7 输出：视频片段
        └── output/
            └── final.mp4            # M8 输出：最终视频
```

## 6. 与旧版文档相比的关键修正

- 状态从"方案设计"改为"已实现（持续迭代）"。
- 目录示例从 `movies/` 改为当前仓库实际结构（`Movie/` 根目录）。
- 保留 M1-M8 模块划分，但补充了每个模块的**降级路径**（当前代码已实现）。
- 明确存在双入口：CLI（`main.py`）与 Gradio（`gradio_app.py`）。
- 明确 M3 当前会清理模型来源字段，且 synopsis 目标受配置项约束。
- 删除"第一步行动建议"这类搭建期内容，改为实现快照口径。
- **新增** 关键场景模式（`run_keyscene`）的完整说明，包括命令参数、流程、与标准模式的对比。
- **新增** M3 关键场景生成和时间戳匹配的详细架构说明。
- **新增** M4 剧本导出功能的说明。
- **新增** CLI 命令速查表。

## 7. 当前仍在迭代的点

- M3 质量阈值目前是"接近千字"（默认 900-1200 范围），如需强制 >=1000 可进一步上调校验阈值。
- 配置中 API key 目前直接出现在 `conf.py`，后续建议迁移到环境变量注入。
- **关键场景模式**已完整集成，LLM 增强和时间戳匹配逻辑已整合到模块二中。
- 关键场景数量目前固定 20 个，后续可考虑根据电影时长动态调整。
- 时间戳匹配目前基于字幕文本，后续可探索结合画面内容的视觉匹配。

---

## 附录 A：CLI 命令速查表

### 快速开始

```bash
# 查看帮助
python main.py --help
python main.py run --help
python main.py run_keyscene --help
python main.py run_keyscene "movie.mp4" --name "电影名"
python main.py run_keyscene "movie.mp4" --name "电影名" --voice "voice.wav" --stop-at 8

# 从已有 workspace 导出剧本
python main.py export-script "workspace/电影名"

# 恢复执行
python main.py resume "workspace/电影名" --step 4 --video "movie.mp4" --voice "voice.wav"
```

### 常用参数组合

| 场景 | 命令 |
|------|------|
| 生成剧本草稿 | `python main.py run_keyscene "movie.mp4" --name "电影名"` |
| 生成详细 movie_info | `python main.py run_keyscene "movie.mp4" --name "电影名" --stop-at 3` |
| 跳过时间戳匹配 | `python main.py run_keyscene "movie.mp4" --name "电影名" --skip-timestamp-match` |
| 重新导出剧本 | `python main.py export-script "workspace/电影名"` |
| 从 Step 4 继续 | `python main.py resume "workspace/电影名" --step 4 --video "movie.mp4" --voice "voice.wav"` |

### 关键场景模式 --stop-at 参数说明

| 值 | 停止点 | 输出文件 | 需要 voice |
|----|--------|----------|------------|
| 1 | 初始化 | workspace/ 目录 | 否 |
| 2 | 预处理 | subtitles.json, scenes.json | 否 |
| 3 | M3 信息采集 | movie_info.json (20 key_scenes) | 否 |
| **4** | **导出剧本 + 转换格式** | **movie_script_user.md, script.json** | **否** |
| 5 | M5-M7 素材生产 | tts_audio/, bgm_clips/, video_clips/ | 是 |
| 6 | 素材审核 | segments_reviewed.json | 是 |
| 7/8 | M8 最终合成 | output/final.mp4 | 是 |
