from __future__ import annotations

import json
import os
import importlib
from pathlib import Path
from typing import Any

import conf
from modules.bgm_matcher import BGMMatcher
from modules.final_composer import FinalComposer
from modules.info_collector import InfoCollector
from modules.scene_detector import SceneDetector
from modules.script_generator import ScriptGenerator
from modules.subtitle_extractor import SubtitleExtractor
from modules.tts_synthesizer import TTSSynthesizer
from modules.video_editor import VideoEditor


def _init_workspace(movie_name: str) -> str:
    workspace = os.path.join(conf.WORKSPACE_DIR, movie_name)
    for subdir in ["scenes_thumbnails", "tts_audio", "bgm_clips", "video_clips", "output"]:
        os.makedirs(os.path.join(workspace, subdir), exist_ok=True)
    return workspace


def _dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _load_json_text(text: str) -> Any:
    return json.loads(text)


def _merge_segments(base: list[dict[str, Any]], tts: list[dict[str, Any]], bgm: list[dict[str, Any]], video: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for i, seg in enumerate(base):
        item = dict(seg)
        if i < len(tts):
            item["tts_audio_path"] = tts[i].get("tts_audio_path", "")
            item["tts_duration_valid"] = tts[i].get("tts_duration_valid", True)
        if i < len(bgm):
            item["bgm_clip_path"] = bgm[i].get("bgm_clip_path")
        if i < len(video):
            item["video_clip_path"] = video[i].get("video_clip_path", "")
            item["video_clip_duration"] = video[i].get("video_clip_duration", 0.0)
        merged.append(item)
    return merged


def ui_init(movie_path: str, movie_name: str, voice_path: str, state: dict[str, Any]) -> tuple[dict[str, Any], str]:
    try:
        if not movie_name.strip():
            return state, "❌ 电影名不能为空"
        if not os.path.isfile(movie_path):
            return state, f"❌ 电影文件不存在: {movie_path}"
        if not os.path.isfile(voice_path):
            return state, f"❌ 参考音频不存在: {voice_path}"

        workspace = _init_workspace(movie_name.strip())
        new_state = {
            "movie_path": movie_path,
            "movie_name": movie_name.strip(),
            "voice_path": voice_path,
            "workspace": workspace,
        }
        return new_state, f"✅ 初始化完成\nworkspace: {workspace}"
    except Exception as exc:
        return state, f"❌ 初始化失败: {exc}"


def ui_preprocess(state: dict[str, Any]) -> tuple[dict[str, Any], str]:
    try:
        workspace = state.get("workspace", "")
        movie_path = state.get("movie_path", "")
        movie_name = state.get("movie_name", "")
        if not workspace:
            return state, "❌ 请先初始化"

        extractor = SubtitleExtractor(workspace)
        subtitles = extractor.extract(movie_path)
        extractor.save(subtitles, os.path.join(workspace, "subtitles.json"))

        detector = SceneDetector(workspace)
        scenes = detector.detect(movie_path)
        detector.save(scenes, os.path.join(workspace, "scenes.json"))

        collector = InfoCollector()
        movie_info = collector.collect(movie_name)
        collector.save(movie_info, os.path.join(workspace, "movie_info.json"))

        state["subtitles"] = subtitles
        state["scenes"] = scenes
        state["movie_info"] = movie_info

        return state, f"✅ 预处理完成\n字幕: {len(subtitles)} 条\n场景: {len(scenes)} 个"
    except Exception as exc:
        return state, f"❌ 预处理失败: {exc}"


def ui_generate_script(state: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    try:
        workspace = state.get("workspace", "")
        if not workspace:
            return state, "❌ 请先初始化", ""

        subtitles = state.get("subtitles", [])
        scenes = state.get("scenes", [])
        movie_info = state.get("movie_info", {})
        if not subtitles or not scenes:
            return state, "❌ 请先执行预处理", ""

        generator = ScriptGenerator()
        script = generator.generate(subtitles, scenes, movie_info)
        script_path = os.path.join(workspace, "script.json")
        generator.save(script, script_path)

        state["script"] = script
        return state, "✅ 剧本生成完成，请在下方 JSON 编辑区做人工审核", _dump_json(script)
    except Exception as exc:
        return state, f"❌ 剧本生成失败: {exc}", ""


def ui_save_script(state: dict[str, Any], script_json: str) -> tuple[dict[str, Any], str]:
    try:
        workspace = state.get("workspace", "")
        if not workspace:
            return state, "❌ 请先初始化"
        script = _load_json_text(script_json)
        if not isinstance(script, dict) or not isinstance(script.get("segments"), list):
            return state, "❌ script JSON 格式无效，必须包含 segments 数组"

        with open(os.path.join(workspace, "script.json"), "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        state["script"] = script
        return state, f"✅ 剧本已保存，segment 数量: {len(script['segments'])}"
    except Exception as exc:
        return state, f"❌ 保存剧本失败: {exc}"


def ui_production(state: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    try:
        workspace = state.get("workspace", "")
        movie_path = state.get("movie_path", "")
        voice_path = state.get("voice_path", "")
        if not workspace:
            return state, "❌ 请先初始化", ""

        script = state.get("script")
        if not isinstance(script, dict):
            script_path = os.path.join(workspace, "script.json")
            if not os.path.isfile(script_path):
                return state, "❌ 请先生成并保存剧本", ""
            with open(script_path, "r", encoding="utf-8") as f:
                script = json.load(f)

        segments = script.get("segments", [])
        if not isinstance(segments, list) or not segments:
            return state, "❌ script.segments 为空", ""

        synthesizer = TTSSynthesizer()
        tts_segments = synthesizer.synthesize_segments(segments, voice_path, os.path.join(workspace, "tts_audio"))

        matcher = BGMMatcher()
        if matcher.is_library_empty() and conf.BGM_SKIP_IF_EMPTY:
            bgm_segments = [dict(s) for s in segments]
        else:
            bgm_segments = matcher.match_segments(segments, os.path.join(workspace, "bgm_clips"))

        editor = VideoEditor()
        video_segments = editor.cut_segments(movie_path, segments, os.path.join(workspace, "video_clips"))

        merged = _merge_segments(segments, tts_segments, bgm_segments, video_segments)
        state["segments"] = merged

        return state, "✅ 素材生产完成，请在下方 JSON 审核（可移除 BGM / 标记 skip）", _dump_json(merged)
    except Exception as exc:
        return state, f"❌ 素材生产失败: {exc}", ""


def ui_save_materials(state: dict[str, Any], materials_json: str) -> tuple[dict[str, Any], str]:
    try:
        workspace = state.get("workspace", "")
        if not workspace:
            return state, "❌ 请先初始化"
        segments = _load_json_text(materials_json)
        if not isinstance(segments, list):
            return state, "❌ 素材 JSON 必须是数组"

        state["segments"] = segments
        with open(os.path.join(workspace, "segments_reviewed.json"), "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        return state, f"✅ 素材审核结果已保存，segment 数量: {len(segments)}"
    except Exception as exc:
        return state, f"❌ 保存素材失败: {exc}"


def ui_compose(state: dict[str, Any]) -> tuple[dict[str, Any], str, str | None]:
    try:
        workspace = state.get("workspace", "")
        if not workspace:
            return state, "❌ 请先初始化", None

        segments = state.get("segments")
        if not isinstance(segments, list) or not segments:
            reviewed_file = os.path.join(workspace, "segments_reviewed.json")
            if os.path.isfile(reviewed_file):
                with open(reviewed_file, "r", encoding="utf-8") as f:
                    segments = json.load(f)
            else:
                return state, "❌ 请先执行素材生产并保存审核结果", None

        composer = FinalComposer()
        out_path = os.path.join(workspace, "output", "final.mp4")
        final_video = composer.compose_all(segments, out_path)
        state["final_video"] = final_video
        return state, f"✅ 合成完成: {final_video}", final_video
    except Exception as exc:
        return state, f"❌ 合成失败: {exc}", None


def build_demo() -> Any:
    gr = importlib.import_module("gradio")

    with gr.Blocks(title="电影自动解说工作流") as demo:
        gr.Markdown("# 电影自动解说工作流（Gradio）")
        gr.Markdown("按顺序执行：初始化 -> 预处理 -> 剧本生成与审核 -> 素材生产与审核 -> 最终合成")

        state = gr.State({})

        with gr.Row():
            movie_path = gr.Textbox(label="电影文件路径", placeholder="/data/movie.mp4")
            movie_name = gr.Textbox(label="电影名称", placeholder="让子弹飞")
            voice_path = gr.Textbox(label="参考音频路径", placeholder="/data/voice.wav")

        with gr.Row():
            btn_init = gr.Button("1) 初始化")
            btn_preprocess = gr.Button("2) 预处理")
            btn_generate = gr.Button("3) 生成剧本")

        script_json = gr.Code(label="剧本 JSON（人工审核后可直接编辑）", language="json", lines=18)
        btn_save_script = gr.Button("4) 保存剧本审核结果")

        with gr.Row():
            btn_production = gr.Button("5) 生成素材（TTS+BGM+裁切）")

        materials_json = gr.Code(label="素材 JSON（可编辑 skip/bgm_clip_path）", language="json", lines=18)
        btn_save_materials = gr.Button("6) 保存素材审核结果")

        btn_compose = gr.Button("7) 最终合成")
        final_video = gr.Video(label="最终视频")
        status = gr.Textbox(label="状态", lines=6)

        btn_init.click(ui_init, [movie_path, movie_name, voice_path, state], [state, status])
        btn_preprocess.click(ui_preprocess, [state], [state, status])
        btn_generate.click(ui_generate_script, [state], [state, status, script_json])
        btn_save_script.click(ui_save_script, [state, script_json], [state, status])
        btn_production.click(ui_production, [state], [state, status, materials_json])
        btn_save_materials.click(ui_save_materials, [state, materials_json], [state, status])
        btn_compose.click(ui_compose, [state], [state, status, final_video])

    return demo


if __name__ == "__main__":
    app = build_demo()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)
