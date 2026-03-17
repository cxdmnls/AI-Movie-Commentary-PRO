# 单元测试启动说明

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

## 2. 运行全部测试

```bash
pytest
```

## 3. 按模块单独测试

```bash
pytest tests/test_m1_subtitle_extractor.py
pytest tests/test_m2_scene_detector.py
pytest tests/test_m3_info_collector.py
pytest tests/test_m4_script_generator.py
pytest tests/test_m5_tts_synthesizer.py
pytest tests/test_m6_bgm_matcher.py
pytest tests/test_m7_video_editor.py
pytest tests/test_m8_final_composer.py
pytest tests/test_reviewer.py
```

## 4. 推荐依赖顺序（前后依赖）

1. `M1 -> M2 -> M3 -> M4`
2. `M5 -> M6 -> M7 -> M8`
3. `review`

对应命令：

```bash
pytest tests/test_m1_subtitle_extractor.py tests/test_m2_scene_detector.py tests/test_m3_info_collector.py tests/test_m4_script_generator.py
pytest tests/test_m5_tts_synthesizer.py tests/test_m6_bgm_matcher.py tests/test_m7_video_editor.py tests/test_m8_final_composer.py
pytest tests/test_reviewer.py
```
