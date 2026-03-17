"""
电影自动解说工作流 — Pipeline 主控入口
使用 typer CLI 框架，编排 M1-M8 模块 + 用户审核
"""
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel

import conf
from modules.subtitle_extractor import SubtitleExtractor
from modules.scene_detector import SceneDetector
from modules.info_collector import InfoCollector
from modules.script_generator import ScriptGenerator
from modules.tts_synthesizer import TTSSynthesizer
from modules.bgm_matcher import BGMMatcher
from modules.video_editor import VideoEditor
from modules.final_composer import FinalComposer
from review import Reviewer

app = typer.Typer(
    name="movie-narrator",
    help="电影自动解说工作流 — 输入电影，输出解说短视频",
    add_completion=False,
)
console = Console()
logger = logging.getLogger("pipeline")


def setup_logging() -> None:
    """配置全局日志"""
    logging.basicConfig(
        level=getattr(logging, conf.LOG_LEVEL),
        format=conf.LOG_FORMAT,
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def init_workspace(movie_name: str) -> str:
    """初始化工作目录，返回 workspace 路径"""
    workspace = os.path.join(conf.WORKSPACE_DIR, movie_name)
    subdirs = [
        "scenes_thumbnails",
        "tts_audio",
        "bgm_clips",
        "video_clips",
        "output",
    ]
    for subdir in subdirs:
        os.makedirs(os.path.join(workspace, subdir), exist_ok=True)
    logger.info("工作目录已初始化: %s", workspace)
    return workspace


def step_parallel_preprocess(
    video_path: str, movie_name: str, workspace: str
) -> tuple[list[dict], list[dict], dict]:
    """Step 2: 并行执行 M1(字幕提取) + M2(场景检测) + M3(信息采集)"""
    console.print(Panel("Step 2: 并行预处理 — 字幕提取 + 场景检测 + 信息采集", style="bold cyan"))

    results = {}

    def run_m1() -> list[dict]:
        """M1 字幕提取"""
        logger.info("[M1] 开始字幕提取...")
        extractor = SubtitleExtractor(workspace)
        subtitles = extractor.extract(video_path)
        output_path = os.path.join(workspace, "subtitles.json")
        extractor.save(subtitles, output_path)
        logger.info("[M1] 字幕提取完成，共 %d 条", len(subtitles))
        return subtitles

    def run_m2() -> list[dict]:
        """M2 场景检测"""
        logger.info("[M2] 开始场景检测...")
        detector = SceneDetector(workspace)
        scenes = detector.detect(video_path)
        output_path = os.path.join(workspace, "scenes.json")
        detector.save(scenes, output_path)
        logger.info("[M2] 场景检测完成，共 %d 个场景", len(scenes))
        return scenes

    def run_m3() -> dict:
        """M3 信息采集"""
        logger.info("[M3] 开始信息采集...")
        collector = InfoCollector()
        info = collector.collect(movie_name)
        output_path = os.path.join(workspace, "movie_info.json")
        collector.save(info, output_path)
        logger.info("[M3] 信息采集完成")
        return info

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(run_m1): "subtitles",
            executor.submit(run_m2): "scenes",
            executor.submit(run_m3): "movie_info",
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.error("[%s] 执行失败: %s", key, e)
                raise typer.Exit(code=1) from e

    return results["subtitles"], results["scenes"], results["movie_info"]


def step_script_generation(
    subtitles: list[dict],
    scenes: list[dict],
    movie_info: dict,
    workspace: str,
) -> dict:
    """Step 3: M4 剧本生成 + 用户审核"""
    console.print(Panel("Step 3: 剧本生成（Qwen API）", style="bold cyan"))

    generator = ScriptGenerator()
    script_path = os.path.join(workspace, "script.json")
    reviewer = Reviewer(workspace)

    for regenerate_round in range(3):
        script = generator.generate(subtitles, scenes, movie_info)
        generator.save(script, script_path)
        segments_value = script.get("segments", [])
        segment_count = len(segments_value) if isinstance(segments_value, list) else 0
        logger.info("[M4] 剧本生成完成，共 %d 个片段", segment_count)

        # ★ 审核点 1：用户审核剧本
        console.print(Panel("★ 审核点 1：请审核剧本", style="bold yellow"))
        reviewed_script = reviewer.review_script(script)
        if reviewed_script.get("regenerate"):
            logger.info("[审核] 用户选择重新生成剧本（第 %d 次）", regenerate_round + 1)
            continue

        generator.save(reviewed_script, script_path)
        logger.info("[审核] 剧本审核完成")
        return reviewed_script

    console.print("[red]错误：剧本审核多次要求重新生成，已达到最大次数。[/red]")
    raise typer.Exit(code=1)


def step_parallel_production(
    video_path: str,
    script: dict,
    voice_ref: str,
    workspace: str,
) -> list[dict]:
    """Step 4: 并行执行 M5(TTS) + M6(BGM) + M7(视频裁切)"""
    console.print(Panel("Step 4: 并行生产 — TTS合成 + BGM匹配 + 视频裁切", style="bold cyan"))

    segments = script["segments"]
    results = {}

    def run_m5() -> list[dict]:
        """M5 TTS 合成"""
        logger.info("[M5] 开始 TTS 合成...")
        synthesizer = TTSSynthesizer()
        tts_dir = os.path.join(workspace, "tts_audio")
        updated = synthesizer.synthesize_segments(segments, voice_ref, tts_dir)
        logger.info("[M5] TTS 合成完成")
        return updated

    def run_m6() -> list[dict]:
        """M6 BGM 匹配"""
        logger.info("[M6] 开始 BGM 匹配...")
        matcher = BGMMatcher()
        if matcher.is_library_empty():
            logger.info("[M6] BGM 曲库为空，跳过 BGM")
            return segments
        bgm_dir = os.path.join(workspace, "bgm_clips")
        updated = matcher.match_segments(segments, bgm_dir)
        logger.info("[M6] BGM 匹配完成")
        return updated

    def run_m7() -> list[dict]:
        """M7 视频裁切"""
        logger.info("[M7] 开始视频裁切...")
        editor = VideoEditor()
        clips_dir = os.path.join(workspace, "video_clips")
        updated = editor.cut_segments(video_path, segments, clips_dir)
        logger.info("[M7] 视频裁切完成")
        return updated

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(run_m5): "tts",
            executor.submit(run_m6): "bgm",
            executor.submit(run_m7): "video",
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.error("[%s] 执行失败: %s", key, e)
                raise typer.Exit(code=1) from e

    # 合并三个模块的输出到 segments
    merged_segments = []
    for i, seg in enumerate(segments):
        merged = {**seg}
        # 从各模块结果中提取路径信息
        if i < len(results["tts"]):
            merged["tts_audio_path"] = results["tts"][i].get("tts_audio_path", "")
        if i < len(results["bgm"]):
            merged["bgm_clip_path"] = results["bgm"][i].get("bgm_clip_path", "")
        if i < len(results["video"]):
            merged["video_clip_path"] = results["video"][i].get("video_clip_path", "")
        merged_segments.append(merged)

    return merged_segments


def step_review_materials(
    segments: list[dict], workspace: str
) -> list[dict]:
    """Step 5: 用户审核素材"""
    console.print(Panel("★ 审核点 2：请审核素材（视频片段 + TTS音频 + BGM）", style="bold yellow"))
    reviewer = Reviewer(workspace)
    segments = reviewer.review_materials(segments)
    logger.info("[审核] 素材审核完成")
    return segments


def step_final_compose(segments: list[dict], workspace: str) -> str:
    """Step 6: M8 最终合成"""
    console.print(Panel("Step 6: 最终合成", style="bold cyan"))

    composer = FinalComposer()
    output_path = os.path.join(workspace, "output", "final.mp4")
    result = composer.compose_all(segments, output_path)
    logger.info("[M8] 最终合成完成: %s", result)
    return result


@app.command()
def run(
    video: str = typer.Argument(..., help="电影文件路径（mp4/mkv）"),
    name: str = typer.Option(..., "--name", "-n", help="电影名称（用于信息采集和工作目录命名）"),
    voice: str = typer.Option(..., "--voice", "-v", help="参考音频路径（用于 TTS voice cloning）"),
) -> None:
    """运行完整的电影解说生成流程"""
    setup_logging()

    # 校验输入
    if not os.path.isfile(video):
        console.print(f"[red]错误：电影文件不存在: {video}[/red]")
        raise typer.Exit(code=1)
    if not os.path.isfile(voice):
        console.print(f"[red]错误：参考音频不存在: {voice}[/red]")
        raise typer.Exit(code=1)

    console.print(Panel(
        f"电影: {name}\n文件: {video}\n音色: {voice}",
        title="电影自动解说工作流",
        style="bold green",
    ))

    # Step 1: 初始化
    workspace = init_workspace(name)

    # Step 2: 并行预处理
    subtitles, scenes, movie_info = step_parallel_preprocess(video, name, workspace)

    # Step 3: 剧本生成 + 审核
    script = step_script_generation(subtitles, scenes, movie_info, workspace)

    # Step 4: 并行生产
    segments = step_parallel_production(video, script, voice, workspace)

    # Step 5: 素材审核
    segments = step_review_materials(segments, workspace)

    # Step 6: 最终合成
    output = step_final_compose(segments, workspace)

    console.print(Panel(
        f"输出文件: {output}",
        title="✅ 完成",
        style="bold green",
    ))


@app.command()
def resume(
    workspace_path: str = typer.Argument(..., help="已有的 workspace 目录路径"),
    step: int = typer.Option(2, "--step", "-s", help="从第几步开始（2-6）"),
    video: str = typer.Option("", "--video", help="电影文件路径（step 2/4 需要）"),
    voice: str = typer.Option("", "--voice", "-v", help="参考音频路径（step 4 需要）"),
) -> None:
    """从指定步骤恢复执行（用于中断后继续）"""
    setup_logging()

    if not os.path.isdir(workspace_path):
        console.print(f"[red]错误：workspace 不存在: {workspace_path}[/red]")
        raise typer.Exit(code=1)

    workspace = workspace_path
    movie_name = os.path.basename(workspace)

    # 尝试加载已有数据
    def load_json(filename: str) -> dict | list | None:
        path = os.path.join(workspace, filename)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    subtitles_data = load_json("subtitles.json")
    scenes_data = load_json("scenes.json")
    movie_info_data = load_json("movie_info.json")
    script_data = load_json("script.json")

    subtitles = subtitles_data if isinstance(subtitles_data, list) else []
    scenes = scenes_data if isinstance(scenes_data, list) else []
    movie_info = movie_info_data if isinstance(movie_info_data, dict) else {}
    script = script_data if isinstance(script_data, dict) else {}

    if step <= 2 and video:
        subtitles, scenes, movie_info = step_parallel_preprocess(video, movie_name, workspace)

    if step <= 3:
        script = step_script_generation(subtitles, scenes, movie_info, workspace)

    if step <= 4:
        if not video:
            console.print("[red]错误：step 4 需要 --video 参数[/red]")
            raise typer.Exit(code=1)
        if not voice:
            console.print("[red]错误：step 4 需要 --voice 参数[/red]")
            raise typer.Exit(code=1)
        segments = step_parallel_production(video, script, voice, workspace)
    else:
        segments = script.get("segments", [])

    if step <= 5:
        segments = step_review_materials(segments, workspace)

    if step <= 6:
        output = step_final_compose(segments, workspace)
        console.print(Panel(f"输出文件: {output}", title="✅ 完成", style="bold green"))


@app.command()
def run_keyscene(
    video: str = typer.Argument(..., help="电影文件路径（mp4/mkv）"),
    name: str = typer.Option(..., "--name", "-n", help="电影名称（用于信息采集和工作目录命名）"),
    voice: str = typer.Option(None, "--voice", "-v", help="参考音频路径（M5-M8需要，用于TTS voice cloning）"),
    stop_at: int = typer.Option(4, "--stop-at", "-s", help="停止步骤: 2=预处理, 3=M3信息采集, 4=M4剧本(默认), 5=M5-7素材生产, 8=M8最终合成"),
    skip_timestamp_match: bool = typer.Option(False, "--skip-timestamp-match", help="跳过字幕时间戳匹配"),
) -> None:
    """使用关键场景模式运行（基于剧情关键场景而非画面检测）"""
    setup_logging()

    # 校验输入
    if not os.path.isfile(video):
        console.print(f"[red]错误：电影文件不存在: {video}[/red]")
        raise typer.Exit(code=1)
    
    # 如果需要运行到M5-M8，必须提供voice
    if stop_at >= 5 and not voice:
        console.print(f"[red]错误：运行到步骤 {stop_at} 需要提供 --voice 参数[/red]")
        raise typer.Exit(code=1)
    
    # 如果提供了voice，校验文件存在
    if voice and not os.path.isfile(voice):
        console.print(f"[red]错误：参考音频不存在: {voice}[/red]")
        raise typer.Exit(code=1)

    status_msg = f"电影: {name}\n文件: {video}\n模式: 关键场景模式"
    if voice:
        status_msg += f"\n音色: {voice}"
    status_msg += f"\n停止步骤: {stop_at}"
    
    console.print(Panel(
        status_msg,
        title="电影自动解说工作流（关键场景模式）",
        style="bold green",
    ))

    # Step 1: 初始化
    workspace = init_workspace(name)
    logger.info("工作目录已初始化: %s", workspace)
    
    if stop_at <= 1:
        console.print(Panel(
            f"✅ 已完成到 Step 1（初始化）\n"
            f"工作目录: {workspace}",
            title="运行完成",
            style="bold green",
        ))
        return

    # Step 2: 并行预处理（M1 + M2）
    console.print(Panel("Step 2: 并行预处理 — 字幕提取 + 场景检测", style="bold cyan"))
    
    # M1: 字幕提取（关键场景模式必需，用于时间戳匹配）
    logger.info("[M1] 开始字幕提取...")
    extractor = SubtitleExtractor(workspace)
    subtitles = extractor.extract(video)
    extractor.save(subtitles, os.path.join(workspace, "subtitles.json"))
    logger.info("[M1] 字幕提取完成，共 %d 条", len(subtitles))
    
    # M2: 场景检测（可选，用于参考）
    logger.info("[M2] 开始场景检测...")
    detector = SceneDetector(workspace)
    scenes = detector.detect(video)
    detector.save(scenes, os.path.join(workspace, "scenes.json"))
    logger.info("[M2] 场景检测完成，共 %d 个场景", len(scenes))

    if stop_at <= 2:
        console.print(Panel(
            f"✅ 已完成到 Step 2\n"
            f"字幕: {workspace}/subtitles.json ({len(subtitles)} 条)\n"
            f"场景: {workspace}/scenes.json ({len(scenes)} 个)",
            title="运行完成",
            style="bold green",
        ))
        return

    # Step 3: M3 信息采集（关键场景模式）
    console.print(Panel("Step 3: 信息采集（关键场景模式）", style="bold cyan"))
    logger.info("[M3] 开始信息采集（关键场景模式）...")
    collector = InfoCollector()
    movie_info = collector.collect(
        name, 
        keyscene_mode=True,
        subtitles=subtitles if not skip_timestamp_match else None
    )
    collector.save(movie_info, os.path.join(workspace, "movie_info.json"))
    logger.info("[M3] 信息采集完成，生成 %d 个关键场景", len(movie_info.get("key_scenes", [])))

    if stop_at <= 3:
        console.print(Panel(
            f"✅ 已完成到 Step 3\n"
            f"输出: {workspace}/movie_info.json\n"
            f"关键场景数: {len(movie_info.get('key_scenes', []))}",
            title="运行完成",
            style="bold green",
        ))
        return

    # Step 3.5: 导出可读剧本
    console.print(Panel("Step 3.5: 导出可读剧本", style="bold cyan"))
    generator = ScriptGenerator()
    script_path = os.path.join(workspace, "movie_script_user.md")
    generator.export_readable_script(movie_info, script_path)
    logger.info("剧本已导出: %s", script_path)

    # Step 4: 基于 key_scenes 生成标准格式的 script.json
    console.print(Panel("Step 4: 转换关键场景为标准剧本格式", style="bold cyan"))
    script = {
        "total_duration_target": f"{conf.SCRIPT_TARGET_DURATION_MIN:02d}:00",
        "segments": []
    }
    
    for i, scene in enumerate(movie_info.get("key_scenes", [])):
        video_clip = scene.get("video_clip", {})
        segment = {
            "segment_id": i + 1,
            "scene_ids": [scene.get("scene_id", i + 1)],
            "video_clip": {
                "start": video_clip.get("start", 0.0),
                "end": video_clip.get("end", 120.0)
            },
            "narration_text": scene.get("summary", ""),
            "emotion": scene.get("suggested_emotion", "平静"),
            "is_climax": scene.get("importance", 5) >= 8,
            "bgm_required": True
        }
        script["segments"].append(segment)
    
    script_path = os.path.join(workspace, "script.json")
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    logger.info("标准剧本已保存，共 %d 个片段", len(script["segments"]))

    if stop_at <= 4:
        console.print(Panel(
            f"✅ 已完成到 Step 4\n"
            f"Movie Info: {workspace}/movie_info.json\n"
            f"可读剧本: {workspace}/movie_script_user.md\n"
            f"标准剧本: {workspace}/script.json",
            title="运行完成",
            style="bold green",
        ))
        return

    # Step 5: 并行生产（M5 + M6 + M7）
    segments = step_parallel_production(video, script, voice, workspace)

    if stop_at <= 5:
        console.print(Panel(
            f"✅ 已完成到 Step 5（素材生产）\n"
            f"输出目录: {workspace}/tts_audio, bgm_clips, video_clips",
            title="运行完成",
            style="bold green",
        ))
        return

    # Step 6: 素材审核
    segments = step_review_materials(segments, workspace)

    # Step 7: 最终合成
    output = step_final_compose(segments, workspace)

    console.print(Panel(
        f"输出文件: {output}",
        title="✅ 完成（关键场景模式）",
        style="bold green",
    ))


@app.command()
def export_script(
    workspace_path: str = typer.Argument(..., help="workspace 目录路径"),
    output: str = typer.Option("", "--output", "-o", help="输出文件路径（默认: workspace/movie_script_user.md）"),
) -> None:
    """从已有 workspace 导出可读剧本（Markdown）"""
    setup_logging()

    if not os.path.isdir(workspace_path):
        console.print(f"[red]错误：workspace 不存在: {workspace_path}[/red]")
        raise typer.Exit(code=1)

    # 加载 movie_info.json
    movie_info_path = os.path.join(workspace_path, "movie_info.json")
    if not os.path.isfile(movie_info_path):
        console.print(f"[red]错误：movie_info.json 不存在: {movie_info_path}[/red]")
        raise typer.Exit(code=1)

    with open(movie_info_path, "r", encoding="utf-8") as f:
        movie_info = json.load(f)

    # 确定输出路径
    if output:
        output_path = output
    else:
        output_path = os.path.join(workspace_path, "movie_script_user.md")

    # 导出剧本
    console.print(Panel("导出可读剧本", style="bold cyan"))
    generator = ScriptGenerator()
    generator.export_readable_script(movie_info, output_path)
    
    console.print(Panel(
        f"✅ 剧本已导出\n"
        f"文件: {output_path}",
        title="导出完成",
        style="bold green",
    ))


if __name__ == "__main__":
    app()
